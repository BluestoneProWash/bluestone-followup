"""Decision engine: turn (jobs, replies, clock) into a list of Actions.

The runner executes the Actions via the RevDek/Quo integration and then calls
the apply_* helpers so state advances exactly once per real send.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable

from . import classify as classify_mod
from . import templates
from . import timing
from .store import (
    STATUS_ESCALATED,
    Store,
)

STOP_WORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit", "stop all"}


def _completed_since(cfg: Any) -> date | None:
    raw = cfg["sending"].get("completed_since")
    if not raw:
        return None
    return date.fromisoformat(str(raw)[:10])


@dataclass
class Action:
    kind: str                       # send_sms | notify_anderson | note
    job_id: str | None
    stage: str                      # checkin | closeout | clarify | escalation
    to: str | None = None
    body: str = ""
    meta: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "job_id": self.job_id,
            "stage": self.stage,
            "to": self.to,
            "body": self.body,
            "meta": self.meta,
        }


# ---------------------------------------------------------------------------
# Stage 1: completed jobs -> schedule + send check-ins
# ---------------------------------------------------------------------------
def plan_checkins(
    jobs: list[dict], now: datetime, cfg: Any, store: Store
) -> list[Action]:
    actions: list[Action] = []
    max_sends = int(cfg["poller"].get("max_sends_per_run", 25))
    respect_opt_outs = cfg["sending"].get("respect_opt_outs", True)
    allowlist = set(cfg["sending"].get("job_allowlist") or [])
    since = _completed_since(cfg)

    for job in jobs:
        if not job.get("completed"):
            continue
        job_id = job.get("job_id")
        if not job_id:
            continue
        if allowlist and job_id not in allowlist:
            continue

        row = store.get_followup(job_id)
        if row is None:
            now_ct = timing.to_ct(now, cfg)
            if since is not None and now_ct.date() < since:
                continue  # too old - don't back-fill history
            # first time we've seen this job completed -> that's the completion moment
            send_time = timing.compute_send_time(now, cfg)
            store.create_followup(job, send_time.isoformat(), seen_at=now_ct.isoformat())
            row = store.get_followup(job_id)

        if row["checkin_sent_at"] is not None:
            continue  # idempotency: already sent
        if row["status"] != "scheduled":
            continue

        phone = job.get("customer_phone") or row["customer_phone"]
        if not phone:
            actions.append(Action("note", job_id, "checkin", meta={"skipped": "no phone number"}))
            continue
        if respect_opt_outs and store.is_opted_out(phone):
            store.set_status(job_id, "opted_out")
            continue

        send_time = datetime.fromisoformat(row["send_time"])
        if not timing.is_due(send_time, now, cfg):
            continue

        body = templates.render_check_in(job, cfg)
        actions.append(Action("send_sms", job_id, "checkin", to=phone, body=body,
                              meta={"send_time": row["send_time"]}))
        if len([a for a in actions if a.kind == "send_sms"]) >= max_sends:
            break

    return actions


def apply_checkin_sent(job_id: str, body: str, to: str, cfg: Any, store: Store,
                       provider_message_id: str | None = None) -> None:
    dry = cfg["sending"].get("dry_run", True)
    store.log_message(job_id, "outbound", "checkin", body, to_number=to,
                      dry_run=dry, provider_message_id=provider_message_id)
    store.mark_checkin_sent(job_id)


# ---------------------------------------------------------------------------
# Stage 2: inbound reply -> classify -> branch
# ---------------------------------------------------------------------------
def handle_reply(
    job: dict,
    reply_text: str,
    now: datetime,
    cfg: Any,
    store: Store,
    classifier: Callable[[str, Any], tuple[str, str]] | None = None,
    classification: str | None = None,
) -> list[Action]:
    """job must be the normalized job dict; its job_id must already have a followup row."""
    job_id = job["job_id"]
    row = store.get_followup(job_id)
    if row is None:
        return [Action("note", job_id, "reply", meta={"error": "no followup row for reply"})]

    phone = job.get("customer_phone") or row["customer_phone"]
    text = (reply_text or "").strip()

    # opt-out handling (Quo also does this natively; we mirror it)
    if text.lower().strip(" .!") in STOP_WORDS:
        if phone:
            store.add_opt_out(phone)
        store.set_status(job_id, "opted_out")
        store.log_message(job_id, "inbound", "reply", text, to_number=phone)
        return [Action("note", job_id, "reply", meta={"opted_out": phone})]

    if store.already_logged_inbound(job_id, text):
        return []  # already processed this exact reply

    store.log_message(job_id, "inbound", "reply", text, to_number=phone)

    if row["status"] not in ("awaiting_reply",):
        return [Action("note", job_id, "reply", meta={"ignored": f"status={row['status']}"})]

    # Deterministic pre-check: "call me" / "have someone reach out" -> straight to
    # Anderson, no clarifying reply, whatever the sentiment. Runs before (and
    # overrides) classification so it can't be missed.
    cr_phrase = classify_mod.looks_like_contact_request(text, cfg)
    if cr_phrase:
        store.log_classification(job_id, text, "CONTACT_REQUEST",
                                 f"contact-request phrase: {cr_phrase!r}", "rulebased")
        return _contact_request_actions(job, text, cfg, store)

    # classify
    if classification is not None:
        result, reason = classify_mod.normalize(classification)
        decided_by = "claude"
    elif classifier is not None:
        result, reason = classifier(text, cfg)
        decided_by = "claude"
    else:
        result, reason = classify_mod.classify_rulebased(text, cfg)
        decided_by = "rulebased"

    store.log_classification(job_id, text, result, reason, decided_by)

    if result == "CONTACT_REQUEST":
        return _contact_request_actions(job, text, cfg, store)
    if result == "SATISFIED":
        delay = int(cfg.get("closeout", {}).get("delay_minutes_after_satisfied", 0))
        due_at = timing.to_ct(now, cfg) + timedelta(minutes=delay)
        store.schedule_closeout(job_id, due_at.isoformat())
        return [Action("note", job_id, "closeout", meta={
            "scheduled_for": due_at.isoformat(), "delay_minutes": delay})]
    if result == "DISSATISFIED":
        return _escalate_actions(job, text, cfg, store, reason="dissatisfied reply")
    # UNCLEAR
    retries = int(row["unclear_retries"])
    limit = int(cfg["classification"].get("unclear_retry_limit", 1))
    if retries >= limit:
        return _escalate_actions(job, text, cfg, store, reason="still unclear after retry")
    store.bump_unclear(job_id)
    body = templates.render_unclear(cfg)
    return [Action("send_sms", job_id, "clarify", to=phone, body=body)]


# ---------------------------------------------------------------------------
# Stage 3: send scheduled closeouts once their delay has elapsed
# ---------------------------------------------------------------------------
def plan_closeouts(jobs: list[dict], now: datetime, cfg: Any, store: Store) -> list[Action]:
    by_id = {j.get("job_id"): j for j in jobs}
    now_ct = timing.to_ct(now, cfg)
    co_cfg = cfg.get("closeout", {}) or {}
    require_quotes = co_cfg.get("require_future_quotes", True)
    hold_alert_hours = float(co_cfg.get("hold_hours_before_alert", 0) or 0)
    actions: list[Action] = []

    for row in store.due_closeouts():
        due = datetime.fromisoformat(row["closeout_due_at"])
        if now_ct < timing.to_ct(due, cfg):
            continue
        job = by_id.get(row["job_id"])
        if job is None:
            actions.append(Action("note", row["job_id"], "closeout",
                                  meta={"waiting": "job data not in this run"}))
            continue

        rendered = templates.render_closeout(job, cfg)

        if require_quotes and not rendered["parsed_quotes"]:
            # tech hasn't added a "future quotes:" section - hold, don't send
            meta = {"held": "no future quotes in job notes yet",
                    "notes_seen": (job.get("notes") or "")[:200]}
            held_for = now_ct - timing.to_ct(due, cfg)
            if (hold_alert_hours
                    and held_for.total_seconds() >= hold_alert_hours * 3600
                    and not row["held_alert_sent_at"]):
                actions.append(_notes_needed_alert(row, cfg))
            actions.append(Action("note", row["job_id"], "closeout", meta=meta))
            continue

        phone = job.get("customer_phone") or row["customer_phone"]
        actions.append(Action("send_sms", row["job_id"], "closeout", to=phone,
                              body=rendered["body"],
                              meta={k: rendered[k] for k in
                                    ("has_quotes", "has_window_block", "parsed_quotes")}))
    return actions


def _notes_needed_alert(row, cfg: Any) -> Action:
    esc = cfg["escalation"]
    to = esc.get("sms_to") if esc.get("method", "sms") == "sms" else esc.get("email_to")
    body = templates.render_notes_needed(
        {"customer_name": row["customer_name"], "service_label": row["service_label"],
         "date_label": row["date_label"]}, cfg)
    return Action("notify_anderson", row["job_id"], "notes_needed", to=to, body=body,
                  meta={"method": esc.get("method", "sms")})


def _escalate_actions(job: dict, reply_text: str, cfg: Any, store: Store, reason: str) -> list[Action]:
    esc = cfg["escalation"]
    body = templates.render_escalation(job, reply_text, cfg)
    to = esc.get("sms_to") if esc.get("method", "sms") == "sms" else esc.get("email_to")
    return [Action("notify_anderson", job["job_id"], "escalation", to=to, body=body,
                   meta={"reason": reason, "method": esc.get("method", "sms")})]


def _contact_request_actions(job: dict, reply_text: str, cfg: Any, store: Store) -> list[Action]:
    esc = cfg["escalation"]
    body = templates.render_contact_request(job, reply_text, cfg)
    to = esc.get("sms_to") if esc.get("method", "sms") == "sms" else esc.get("email_to")
    return [Action("notify_anderson", job["job_id"], "escalation", to=to, body=body,
                   meta={"reason": "customer asked to be contacted",
                         "method": esc.get("method", "sms")})]


def apply_closeout_sent(job_id: str, body: str, to: str, cfg: Any, store: Store,
                        provider_message_id: str | None = None) -> None:
    dry = cfg["sending"].get("dry_run", True)
    store.log_message(job_id, "outbound", "closeout", body, to_number=to,
                      dry_run=dry, provider_message_id=provider_message_id)
    store.mark_closeout_sent(job_id)


def apply_clarify_sent(job_id: str, body: str, to: str, cfg: Any, store: Store) -> None:
    dry = cfg["sending"].get("dry_run", True)
    store.log_message(job_id, "outbound", "clarify", body, to_number=to, dry_run=dry)
    # status stays awaiting_reply; unclear_retries already bumped


def apply_escalation_sent(job_id: str, body: str, to: str, cfg: Any, store: Store) -> None:
    dry = cfg["sending"].get("dry_run", True)
    store.log_message(job_id, "outbound", "escalation", body, to_number=to, dry_run=dry)
    store.set_status(job_id, STATUS_ESCALATED)


def apply_notes_needed_alert_sent(job_id: str, body: str, to: str, cfg: Any, store: Store) -> None:
    dry = cfg["sending"].get("dry_run", True)
    store.log_message(job_id, "outbound", "notes_needed", body, to_number=to, dry_run=dry)
    store.mark_held_alert_sent(job_id)  # status stays closeout_scheduled - still waiting on notes
