"""Reply classification.

In production the scheduled Claude session classifies each reply directly using
classification.system_prompt from config.yml, and passes the result into the
pipeline. This module provides:
  - classify_rulebased(): a keyword fallback for offline tests and dry-runs
  - normalize(): validate/coerce a classification string
  - looks_like_contact_request(): deterministic "they want a call" check that
    runs BEFORE classification so it can't be missed

Buckets: SATISFIED / DISSATISFIED / UNCLEAR / CONTACT_REQUEST
  CONTACT_REQUEST = the customer wants to talk to a person (call/text me back,
  have someone reach out). Escalates straight to Anderson - no clarifying reply.
"""
from __future__ import annotations

import re
from typing import Any

VALID = ("SATISFIED", "DISSATISFIED", "UNCLEAR", "CONTACT_REQUEST")

# "call me", "give me a call", "can someone call", "text me back", "reach out", etc.
_CONTACT_REQUEST = [
    "call me", "call us", "give me a call", "give us a call", "can you call",
    "can someone call", "please call", "could you call", "have someone call",
    "want a call", "need a call", "call back", "callback", "phone me",
    "text me back", "reach out to me", "have someone reach out", "want to talk",
    "need to talk", "want to speak", "speak to someone", "talk to someone",
    "have him call", "have anderson call",
]

_NEGATIVE = [
    "not happy", "unhappy", "disappointed", "disappoint", "not satisfied",
    "not fully", "streak", "spots", "still dirty", "still there", "missed",
    "you missed", "poor", "bad job", "terrible", "awful", "not great",
    "not good", "reschedule", "come back", "redo", "re-do", "damage",
    "broke", "broken", "refund", "complaint", "cracked",
    "left a mess", "worse", "unacceptable", "waste",
]
_POSITIVE = [
    "great", "awesome", "looks great", "look great", "looks good", "look good",
    "perfect", "amazing", "fantastic", "wonderful", "excellent", "love it",
    "loved it", "very happy", "so happy", "really happy", "happy with",
    "good job", "great job", "well done", "nice job", "thank you", "thanks",
    "appreciate", "beautiful", "spotless", "impressed", "10/10", "5 stars",
    "\U0001f44d", "\U0001f64f", "\U0001f60a", "❤️",
]


def _contains_any(text: str, needles: list[str]) -> str | None:
    for n in needles:
        if n in text:
            return n
    return None


def _contact_phrases(cfg: Any | None) -> list[str]:
    if cfg:
        extra = (cfg.get("classification", {}) or {}).get("contact_request_phrases")
        if extra:
            return list(extra)
    return _CONTACT_REQUEST


def looks_like_contact_request(reply_text: str, cfg: Any | None = None) -> str | None:
    """Return the matched phrase if the reply asks for a person to reach out, else None."""
    t = (reply_text or "").lower()
    return _contains_any(t, _contact_phrases(cfg))


def classify_rulebased(reply_text: str, cfg: Any | None = None) -> tuple[str, str]:
    """Return (classification, reason). Conservative: ambiguous -> UNCLEAR."""
    t = (reply_text or "").lower().strip()
    if not t:
        return "UNCLEAR", "empty reply"

    cr = looks_like_contact_request(t, cfg)
    if cr:
        return "CONTACT_REQUEST", f"contact-request phrase: {cr!r}"

    neg = _contains_any(t, _NEGATIVE)
    pos = _contains_any(t, _POSITIVE)

    if neg and not pos:
        return "DISSATISFIED", f"negative phrase: {neg!r}"
    if neg and pos:
        return "UNCLEAR", f"mixed signal: {pos!r} + {neg!r}"
    if pos:
        if t.endswith("?") and len(t) > 12:
            return "UNCLEAR", f"positive phrase {pos!r} but ends with a question"
        return "SATISFIED", f"positive phrase: {pos!r}"
    if re.fullmatch(r"(yes+|yep|yeah|yotal|ok|okay|good|great|\U0001f44d+|\U0001f64f+)\.?!?", t):
        return "SATISFIED", "short affirmative"
    return "UNCLEAR", "no clear positive or negative signal"


def normalize(value: str) -> tuple[str, str]:
    v = (value or "").strip().upper()
    # order matters: "DISSATISFIED" contains the substring "SATISFIED"
    for k in ("CONTACT_REQUEST", "DISSATISFIED", "UNCLEAR", "SATISFIED"):
        if k in v:
            return k, f"model said {value!r}"
    return "UNCLEAR", f"unrecognized classification {value!r}"
