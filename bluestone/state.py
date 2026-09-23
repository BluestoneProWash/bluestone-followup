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


def is_closeout(text: str, job: dict, cfg: Any) -> bool:
    n = _norm(text)
    opener = _norm(templates.render_closeout_opener(job, cfg))
    return bool(opener) and n.startswith(opener)


def is_stop(text: str) -> bool:
    return _norm(text).strip(" .!?") in STOP_WORDS


# ---- per-job state --------------------------------------------------------
def derive(job: dict, thread: list[dict] | None, now: datetime, cfg: Any,
           classifier=None) -> dict:
    """Return the current follow-up state of one job. See _derive_base below
    for the stage list and thread-reading logic; this wrapper applies the
    'Skip to Closing Text' manual override on top of it."""
    st = _derive_base(job, thread, now, cfg, classifier)
    # Manual override: Anderson (or a tech) adds the "Skip to Closing Text"
    # indicator in RevDek - e.g. the customer thanked him directly instead of
    # replying to the automated check-in - and the closeout goes out at the
    # same next-morning time a normal check-in would have, just without ever
    # sending the check-in or waiting on a reply. Never resurrects a job
    # that's opted out, already had its closeout sent/replied-to, or is
    # flagged "Dont Follow Up" - that stays an absolute stop.
    if job.get("force_satisfied") and st["stage"] not in (
        "opted_out", "closed_satisfied", "closeout_reply", "do_not_follow_up"
    ):
        st["stage"] = "closeout_pending"
        st["satisfied_at"] = st.get("satisfied_at") or timing.checkin_due_time(job, cfg) or now
        st["classification"] = "SATISFIED"
        st["classification_reason"] = "manually confirmed via 'Skip to Closing Text' indicator"
    return st


def _derive_base(job: dict, thread: list[dict] | None, now: datetime, cfg: Any,
                  classifier=None) -> dict:
    """Return the current follow-up state of one job.

    stage: no_thread | no_checkin | awaiting_reply | closeout_pending |
           closed_satisfied | closeout_reply | needs_escalation | opted_out |
           pending_inbound | do_not_follow_up

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
                          "replies_after_checkin": [], "replies_after_closeout": [],
                          "closeout_sent": False, "opted_out": False,
                          "last_reply": None, "classification": None}

    if any(is_stop(m["text"]) for m in inb):
        st["stage"] = "opted_out"
        st["opted_out"] = True
        return st

    if job.get("do_not_follow_up"):
        # Never send anything automated on this job. But if the customer
        # texted in and NOTHING was ever sent to them (no check-in on
        # record), Anderson still needs to know - the check-in is meant to
        # sound like him personally, so total silence reads as him ignoring
        # them. If a check-in already went out before the flag was added,
        # they weren't ignored - stay silent, that's the whole point of the flag.
        already_texted = any(is_checkin(m["text"], job, cfg) for m in out)
        if inb and not already_texted:
            st["stage"] = "pending_inbound"
            st["last_reply"] = inb[-1]
        else:
            st["stage"] = "do_not_follow_up"
        return st

    checkin = next((m for m in out if is_checkin(m["text"], job, cfg)), None)
    if checkin is not None:
        st["checkin_at"] = checkin["at"]

    # Messages that count toward a reply/closeout: after the check-in normally,
    # but the "Skip to Closing Text" override never sends a check-in at all -
    # there the closeout itself is the first outbound message, so everything
    # in the thread counts.
    after = [m for m in msgs if checkin is None or m["at"] > checkin["at"]]
    replies = [m for m in after if m["direction"] == "inbound" and not is_stop(m["text"])]
    st["replies_after_checkin"] = replies
    closeout_msg = next((m for m in after
                         if m["direction"] == "outbound" and is_closeout(m["text"], job, cfg)), None)
    st["closeout_sent"] = closeout_msg is not None

    if st["closeout_sent"]:
        # customer messages that arrived AFTER the closeout - the automation is
        # otherwise done with this job, so a reply here goes to Anderson.
        post = [m for m in after if m["direction"] == "inbound"
                and m["at"] > closeout_msg["at"] and not is_stop(m["text"])]
        st["replies_after_closeout"] = post
        if post:
            st["last_reply"] = post[-1]
            st["stage"] = "closeout_reply"
        else:
            st["stage"] = "closed_satisfied"
        return st
    if checkin is None:
        if inb:
            # Customer texted in before the automated check-in ever went out.
            # Sending the canned "how did everything turn out" script on top
            # of an unanswered text would look like Anderson ignored them -
            # hold it and alert him instead.
            st["stage"] = "pending_inbound"
            st["last_reply"] = inb[-1]
        else:
            st["stage"] = "no_thread" if not msgs else "no_checkin"
        return st
    if not replies:
        st["stage"] = "awaiting_reply"
        return st

    last = replies[-1]
    st["last_reply"] = last
    result, reason = _classify(last["text"], cfg, classifier)
    st["classification"] = result
    st["classification_reason"] = reason

    if result == "SATISFIED":
        st["stage"] = "closeout_pending"
        st["satisfied_at"] = last["at"]
    else:
        # DISSATISFIED, CONTACT_REQUEST, and UNCLEAR all go straight to
        # Anderson - no automated clarifying text. Asking "did everything turn
        # out well?" again after an ambiguous or even a clearly negative reply
        # reads as tone-deaf; a person should take it from here instead.
        st["stage"] = "needs_escalation"
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
