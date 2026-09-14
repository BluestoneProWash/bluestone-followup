"""Stateless decision engine.

plan(jobs, threads, now, cfg) -> list[Action]

No database. `threads` is {phone_e164: [ {direction, text, at}, ... ]} covering
every in-scope customer plus the escalation number. State is re-derived from
those threads every run (see state.py), so nothing has to be persisted between
runs and the runner only needs to READ RevDek.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from . import markers as markers_mod
from . import state as state_mod
from . import templates
from . import timing


@dataclass
class Action:
    kind: str            # send_sms | notify_anderson | note
    job_id: str | None
    stage: str           # checkin | closeout | clarify | escalation | closeout_reply
    to: str | None = None
    body: str = ""
    marker: str | None = None       # marker stage to record after this send succeeds
    marker_note_after: str | None = None   # exact indicator note text to write
    marker_indicator: str | None = None    # indicator name to write it on
    meta: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"kind": self.kind, "job_id": self.job_id, "stage": self.stage,
                "to": self.to, "body": self.body, "marker": self.marker,
                "marker_note_after": self.marker_note_after,
                "marker_indicator": self.marker_indicator, "meta": self.meta}


def _to(cfg: Any) -> str | None:
    esc = cfg["escalation"]
    return esc.get("sms_to") if esc.get("method", "sms") == "sms" else esc.get("email_to")


def _in_scope(job: dict, now_ct: datetime, cfg: Any) -> bool:
    s = cfg["sending"]
    if not job.get("completed"):
        return False
    allowlist = set(s.get("job_allowlist") or [])
    if allowlist and job.get("job_id") not in allowlist:
        return False
    d = (job.get("date") or "")[:10]
    if not d:
        return False
    try:
        jd = date.fromisoformat(d)
    except ValueError:
        return False
    since = s.get("completed_since")
    if since and jd < date.fromisoformat(str(since)[:10]):
        return False
    max_age = float(s.get("max_job_age_days", 14) or 0)
    if max_age and (now_ct.date() - jd).days > max_age:
        return False
    return True


def plan(jobs: list[dict], threads: dict[str, list[dict]], now: datetime,
         cfg: Any, classifier=None) -> list[Action]:
    # Hard kill switch. When set, NOTHING is ever sent - no check-in, closeout,
    # clarify, or Anderson alert. Independent of dry_run and the routine's
    # enabled flag. Set while the idempotency rebuild is in progress.
    if cfg["sending"].get("halted"):
        return [Action("note", None, "halted", meta={"halted": True})]

    now_ct = timing.to_ct(now, cfg)
    esc_to = _to(cfg)
    # An unresolved ${VAR} means the env-var prefix was dropped on this call -
    # refuse to do anything rather than let an escalation try to "send" to a
    # literal template string, or proceed on a half-loaded config.
    if not esc_to or "${" in esc_to:
        return [Action("note", None, "config_error",
                       meta={"error": "escalation contact unresolved - "
                                       "BLUESTONE_ESCALATION_SMS/EMAIL env var "
                                       "was not set for this command"})]

    # De-dupe by job_id: two entries for the same job (a sloppy jobs.json merge)
    # must never turn into two send actions in one plan() call - that would
    # both go out before either marker write happens.
    seen_ids: set = set()
    deduped = []
    for j in jobs:
        jid_ = j.get("job_id")
        if jid_ in seen_ids:
            continue
        if jid_ is not None:
            seen_ids.add(jid_)
        deduped.append(j)
    jobs = deduped

    anderson_thread = threads.get(esc_to or "", [])
    max_sends = int(cfg["poller"].get("max_sends_per_run", 25))
    delay_min = int(cfg.get("closeout", {}).get("delay_minutes_after_satisfied", 0))
    ind_name = markers_mod.indicator_name(cfg)
    sent = 0
    actions: list[Action] = []

    def send(kind, jid, stage, to, body, marker, marker_note, meta=None):
        return Action(kind, jid, stage, to=to, body=body, marker=marker,
                      marker_note_after=markers_mod.add(marker_note, marker, now_ct),
                      marker_indicator=ind_name, meta=meta or {})

    for job in jobs:
        jid = job.get("job_id")
        phone = job.get("customer_phone")
        if not phone:
            actions.append(Action("note", jid, "checkin", meta={"skipped": "no phone number"}))
            continue
        if not _in_scope(job, now_ct, cfg):
            continue

        marker_note = markers_mod.job_marker_note(job, cfg)
        marked = markers_mod.parse(marker_note)

        thread = threads.get(phone, [])
        st = state_mod.derive(job, thread, now_ct, cfg, classifier)
        stage = st["stage"]

        if stage in ("opted_out", "closed_satisfied", "awaiting_reply"):
            continue

        if stage == "closeout_reply":
            if not cfg["escalation"].get("notify_on_closeout_reply", True):
                continue
            if "escalated" in marked or state_mod.already_escalated(job, anderson_thread, now_ct, cfg):
                actions.append(Action("note", jid, "closeout_reply",
                                      meta={"skipped": "already alerted Anderson"}))
                continue
            reply_text = st["last_reply"]["text"] if st["last_reply"] else ""
            actions.append(send("notify_anderson", jid, "closeout_reply", esc_to,
                                templates.render_closeout_reply(job, reply_text, cfg),
                                "escalated", marker_note,
                                {"method": cfg["escalation"].get("method", "sms")}))
            continue

        if stage in ("no_thread", "no_checkin"):
            if "checkin" in marked:
                actions.append(Action("note", jid, "checkin",
                                      meta={"skipped": "checkin marker already on job"}))
                continue
            due = timing.checkin_due_time(job, cfg)
            if due is None or now_ct < timing.to_ct(due, cfg):
                continue
            if sent >= max_sends:
                continue
            actions.append(send("send_sms", jid, "checkin", phone,
                                templates.render_check_in(job, cfg),
                                "checkin", marker_note, {"due": due.isoformat()}))
            sent += 1
            continue

        if stage == "closeout_pending":
            if "closeout" in marked:
                actions.append(Action("note", jid, "closeout",
                                      meta={"skipped": "closeout marker already on job"}))
                continue
            ready_at = st["satisfied_at"] + timedelta(minutes=delay_min)
            if now_ct < timing.to_ct(ready_at, cfg):
                actions.append(Action("note", jid, "closeout",
                                      meta={"waiting_until": ready_at.isoformat()}))
                continue
            r = templates.render_closeout(job, cfg)
            actions.append(send("send_sms", jid, "closeout", phone, r["body"],
                                "closeout", marker_note,
                                {k: r[k] for k in ("has_quotes", "has_window_block")}))
            continue

        if stage == "send_clarify":
            if "clarify" in marked:
                actions.append(Action("note", jid, "clarify",
                                      meta={"skipped": "clarify marker already on job"}))
                continue
            actions.append(send("send_sms", jid, "clarify", phone,
                                templates.render_unclear(cfg), "clarify", marker_note))
            continue

        if stage == "needs_escalation":
            if "escalated" in marked or state_mod.already_escalated(job, anderson_thread, now_ct, cfg):
                actions.append(Action("note", jid, "escalation",
                                      meta={"skipped": "already alerted Anderson"}))
                continue
            reply_text = st["last_reply"]["text"] if st["last_reply"] else ""
            if st["classification"] == "CONTACT_REQUEST":
                body = templates.render_contact_request(job, reply_text, cfg)
                reason = "customer asked to be contacted"
            else:
                body = templates.render_escalation(job, reply_text, cfg)
                reason = st.get("classification_reason", "not satisfied")
            actions.append(send("notify_anderson", jid, "escalation", esc_to, body,
                                "escalated", marker_note,
                                {"reason": reason, "method": cfg["escalation"].get("method", "sms")}))
            continue

    return actions
