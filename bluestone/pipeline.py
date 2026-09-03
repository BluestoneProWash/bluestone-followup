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

from . import state as state_mod
from . import templates
from . import timing


@dataclass
class Action:
    kind: str            # send_sms | notify_anderson | note
    job_id: str | None
    stage: str           # checkin | closeout | clarify | escalation | notes_needed
    to: str | None = None
    body: str = ""
    meta: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"kind": self.kind, "job_id": self.job_id, "stage": self.stage,
                "to": self.to, "body": self.body, "meta": self.meta}


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
    now_ct = timing.to_ct(now, cfg)
    esc_to = _to(cfg)
    anderson_thread = threads.get(esc_to or "", [])
    max_sends = int(cfg["poller"].get("max_sends_per_run", 25))
    delay_min = int(cfg.get("closeout", {}).get("delay_minutes_after_satisfied", 0))
    sent = 0
    actions: list[Action] = []

    for job in jobs:
        jid = job.get("job_id")
        phone = job.get("customer_phone")
        if not phone:
            actions.append(Action("note", jid, "checkin", meta={"skipped": "no phone number"}))
            continue
        if not _in_scope(job, now_ct, cfg):
            continue

        thread = threads.get(phone, [])
        st = state_mod.derive(job, thread, now_ct, cfg, classifier)
        stage = st["stage"]

        if stage in ("opted_out", "closed_satisfied", "awaiting_reply"):
            continue

        if stage in ("no_thread", "no_checkin"):
            due = timing.checkin_due_time(job, cfg)
            if due is None or now_ct < timing.to_ct(due, cfg):
                continue
            if sent >= max_sends:
                continue
            actions.append(Action("send_sms", jid, "checkin", to=phone,
                                  body=templates.render_check_in(job, cfg),
                                  meta={"due": due.isoformat()}))
            sent += 1
            continue

        if stage == "closeout_pending":
            ready_at = st["satisfied_at"] + timedelta(minutes=delay_min)
            if now_ct < timing.to_ct(ready_at, cfg):
                actions.append(Action("note", jid, "closeout",
                                      meta={"waiting_until": ready_at.isoformat()}))
                continue
            r = templates.render_closeout(job, cfg)
            actions.append(Action("send_sms", jid, "closeout", to=phone, body=r["body"],
                                  meta={k: r[k] for k in ("has_quotes", "has_window_block")}))
            continue

        if stage == "send_clarify":
            actions.append(Action("send_sms", jid, "clarify", to=phone,
                                  body=templates.render_unclear(cfg)))
            continue

        if stage == "needs_escalation":
            if state_mod.already_escalated(job, anderson_thread, now_ct, cfg):
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
            actions.append(Action("notify_anderson", jid, "escalation", to=esc_to,
                                  body=body, meta={"reason": reason,
                                                   "method": cfg["escalation"].get("method", "sms")}))
            continue

    return actions
