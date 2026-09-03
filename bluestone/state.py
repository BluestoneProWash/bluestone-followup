"""Derive per-job follow-up state from RevDek conversation history.

No database. Everything we need to decide what to do next - has the customer
been texted? did they reply? did we already close them out or escalate? - is
read back out of the message threads each run.

A `thread` is a list of messages, each:  {"direction": "inbound"|"outbound",
"text": str, "at": datetime}  - order doesn't matter, we sort by `at`.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from . import classify as classify_mod
from . import templates
from . import timing

STOP_WORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit", "stop all", "opt out", "optout"}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


# ---- message-type signatures (outbound messages we sent) -------------------
def is_checkin(text: str, job: dict, cfg: Any) -> bool:
    n = _norm(text)
    if not n:
        return False
    rendered = _norm(templates.render_check_in(job, cfg))
    if n == rendered:
        return True
    # tolerate a changed first name: match the part after "this is <owner>."
    tail = rendered.split(".", 1)[-1].strip()
    return bool(tail) and tail in n


def is_closeout(text: str, cfg: Any) -> bool:
    n = _norm(text)
    opener = _norm(cfg["templates"]["closeout_no_quotes"].split("\n", 1)[0])
    return bool(opener) and n.startswith(opener)


def is_clarify(text: str, cfg: Any) -> bool:
    return _norm(templates.render_unclear(cfg)) in _norm(text)


def is_stop(text: str) -> bool:
    return _norm(text).strip(" .!?") in STOP_WORDS


# ---- per-job state --------------------------------------------------------
def derive(job: dict, thread: list[dict] | None, now: datetime, cfg: Any,
           classifier=None) -> dict:
    """Return the current follow-up state of one job.

    stage: no_thread | no_checkin | awaiting_reply | closeout_pending |
           closed_satisfied | clarifying | escalated | opted_out

    Only messages from at/after this job happened are considered - a prior
    follow-up cycle with the same customer (or unrelated older chatter) must not
    look like THIS job's check-in / reply / closeout.
    """
    msgs = sorted(thread or [], key=lambda m: m["at"])
    cutoff = timing.job_completion_time(job, cfg)
    if cutoff is not None:
        cutoff = timing.to_ct(cutoff, cfg)
        msgs = [m for m in msgs if timing.to_ct(m["at"], cfg) >= cutoff]
    out = [m for m in msgs if m["direction"] == "outbound"]
    inb = [m for m in msgs if m["direction"] == "inbound"]

    st: dict[str, Any] = {"stage": "no_checkin", "checkin_at": None,
                          "replies_after_checkin": [], "clarify_count": 0,
                          "closeout_sent": False, "opted_out": False,
                          "last_reply": None, "classification": None}

    if any(is_stop(m["text"]) for m in inb):
        st["stage"] = "opted_out"
        st["opted_out"] = True
        return st

    checkin = next((m for m in out if is_checkin(m["text"], job, cfg)), None)
    if checkin is None:
        st["stage"] = "no_thread" if not msgs else "no_checkin"
        return st
    st["checkin_at"] = checkin["at"]

    after = [m for m in msgs if m["at"] > checkin["at"]]
    replies = [m for m in after if m["direction"] == "inbound" and not is_stop(m["text"])]
    st["replies_after_checkin"] = replies
    st["clarify_count"] = sum(1 for m in after
                              if m["direction"] == "outbound" and is_clarify(m["text"], cfg))
    st["closeout_sent"] = any(m["direction"] == "outbound" and is_closeout(m["text"], cfg)
                              for m in after)

    if st["closeout_sent"]:
        st["stage"] = "closed_satisfied"
        return st
    if not replies:
        st["stage"] = "awaiting_reply"
        return st

    last = replies[-1]
    st["last_reply"] = last
    result, reason = _classify(last["text"], cfg, classifier)
    st["classification"] = result
    st["classification_reason"] = reason

    # have we already sent something in reply to this latest customer message?
    responded_after_last = any(m["direction"] == "outbound" and m["at"] > last["at"]
                               for m in after)
    limit = int(cfg["classification"].get("unclear_retry_limit", 1))

    if result == "SATISFIED":
        st["stage"] = "closeout_pending"
        st["satisfied_at"] = last["at"]
    elif result in ("DISSATISFIED", "CONTACT_REQUEST"):
        st["stage"] = "needs_escalation"
    elif responded_after_last:
        # UNCLEAR but we already sent the clarify - wait for their next reply
        st["stage"] = "awaiting_reply"
    elif st["clarify_count"] < limit:
        st["stage"] = "send_clarify"
    else:
        st["stage"] = "needs_escalation"
        st["classification_reason"] = f"still unclear after {st['clarify_count']} clarify"
    return st


def _classify(text: str, cfg: Any, classifier) -> tuple[str, str]:
    cr = classify_mod.looks_like_contact_request(text, cfg)
    if cr:
        return "CONTACT_REQUEST", f"contact-request phrase: {cr!r}"
    if classifier is not None:
        return classifier(text, cfg)
    return classify_mod.classify_rulebased(text, cfg)


# ---- escalation idempotency: the alert SMS to Anderson is the marker ------
def already_escalated(job: dict, anderson_thread: list[dict] | None, now: datetime,
                      cfg: Any) -> bool:
    """True if Anderson was already texted about this job's customer recently."""
    if not anderson_thread:
        return False
    window_days = float(cfg["escalation"].get("dedupe_days", 30))
    phone_digits = re.sub(r"\D", "", job.get("customer_phone") or "")
    name = _norm(job.get("customer_name") or "")
    for m in anderson_thread:
        if m["direction"] != "outbound":
            continue
        age_days = (now - m["at"]).total_seconds() / 86400
        if age_days > window_days:
            continue
        body_digits = re.sub(r"\D", "", m["text"])
        if phone_digits and phone_digits[-10:] in body_digits:
            return True
        if name and len(name) > 3 and name in _norm(m["text"]):
            return True
    return False
