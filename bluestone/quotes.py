"""Parse future-service quotes for a job.

Priority: the "Closing Quotes Given" job INDICATOR, then the job NOTES.

Indicator format (what techs actually type), one per line:
    Windows -$300
    Roof-                <- service but no price -> skipped
    House Wash - $345

Notes format (fallback):
    "future quotes: roof wash $700, driveway pressure wash: $300"

Tolerated either way: "$700" / "$700.00" / "$1,200", colon/dash before price,
price-first ("$300 windows"), commas / newlines / semicolons as separators.
An entry is only kept if it has BOTH a service name AND a price.
"""
from __future__ import annotations

import re
from typing import Any

_HEADER_RE = re.compile(r"future\s+quotes?\s*[:\-–]?\s*", re.IGNORECASE)
_NUM = r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)"
_PRICE_END_RE = re.compile(r"\$?\s*" + _NUM + r"\s*$")          # "roof wash $700"
_PRICE_START_RE = re.compile(r"^\$\s*" + _NUM + r"\s*[:\-–]?\s*(.+)$")  # "$700 roof wash"

CLOSING_QUOTES_INDICATOR = "closing quotes"   # matched case-insensitively in the name


def _parse_fragments(text: str) -> list[dict]:
    """Split on commas/newlines/semicolons, keep only 'service + price' pieces."""
    if not text:
        return []
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)   # thousands separators
    out: list[dict] = []
    for frag in re.split(r"[,\n;]+", text):
        frag = frag.strip().strip(".").strip()
        if not frag:
            continue
        amount = service = None
        pm = _PRICE_END_RE.search(frag)
        if pm:
            amount = pm.group(1)
            service = frag[: pm.start()].strip().rstrip(":-– ").strip()
        else:
            pm = _PRICE_START_RE.match(frag)
            if pm:
                amount = pm.group(1)
                service = pm.group(2).strip().lstrip(":-– ").strip()
        if not amount or not service:
            continue   # must have BOTH a service and a price
        amount = amount.replace(",", "")
        if amount.endswith(".00"):
            amount = amount[:-3]
        out.append({"service": service, "amount": amount})
    return out


def parse_future_quotes(notes: str | None, cfg: Any) -> list[dict]:
    """Quotes from the job NOTES 'future quotes:' section. [] if no section."""
    if not notes:
        return []
    header = cfg["quote_parsing"].get("section_header", "future quotes")
    hdr_re = re.compile(re.escape(header).replace(r"\ ", r"\s+") + r"\s*[:\-–]?\s*", re.IGNORECASE)
    m = hdr_re.search(notes) or _HEADER_RE.search(notes)
    if not m:
        return []
    tail = notes[m.end():]
    tail = re.split(r"\n\s*\n", tail, maxsplit=1)[0]   # stop at a blank line
    return _parse_fragments(tail)


def parse_closing_quotes(indicator_note: str | None, cfg: Any) -> list[dict]:
    """Quotes from the 'Closing Quotes Given' indicator's note text."""
    return _parse_fragments(indicator_note or "")


def parse_job_quotes(job: dict, cfg: Any) -> list[dict]:
    """The quotes to use for a job: indicator first, notes as fallback."""
    note = _closing_quotes_note(job)
    if note:
        q = parse_closing_quotes(note, cfg)
        if q:
            return q
    return parse_future_quotes(job.get("notes"), cfg)


def _closing_quotes_note(job: dict) -> str | None:
    # normalize_job stashes this; also tolerate a raw indicators array
    if job.get("closing_quotes_note"):
        return job["closing_quotes_note"]
    for ind in job.get("indicators") or []:
        if CLOSING_QUOTES_INDICATOR in (ind.get("name") or "").lower():
            return ind.get("notes")
    return None


def render_quote_list(quotes: list[dict], cfg: Any) -> str:
    qp = cfg["quote_parsing"]
    line_format = qp.get("line_format", "- [service]: $[amount]")
    titlecase = qp.get("titlecase_service", True)
    lines = []
    for q in quotes:
        service = _titlecase(q["service"]) if titlecase else q["service"]
        lines.append(line_format.replace("[service]", service).replace("[amount]", q["amount"]))
    return "\n".join(lines)


def _titlecase(text: str) -> str:
    return " ".join(w if w.isupper() else w.capitalize() for w in text.split())
