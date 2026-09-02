"""Parse the tech's 'future quotes' section out of a job's notes.

Real example that must parse:
    "future quotes: roof wash $700, driveway pressure wash: $300"

Tolerated variations:
    - "future quotes -" / "Future Quotes:" / "future quote:" (case, punctuation)
    - "roof wash $700"  and  "roof wash: $700"  and  "roof wash - $700"
    - "$700", "$700.00", "$1,200", "700"
    - trailing period, extra whitespace, newlines instead of commas
"""
from __future__ import annotations

import re
from typing import Any

# match the header, allowing "quote" or "quotes", any trailing separator
_HEADER_RE = re.compile(r"future\s+quotes?\s*[:\-–]?\s*", re.IGNORECASE)
_NUM = r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)"
# "roof wash $700"  /  "roof wash: 700"
_PRICE_END_RE = re.compile(r"\$?\s*" + _NUM + r"\s*$")
# "$700 roof wash"  /  "700 - roof wash"
_PRICE_START_RE = re.compile(r"^\$\s*" + _NUM + r"\s*[:\-–]?\s*(.+)$")


def parse_future_quotes(notes: str | None, cfg: Any) -> list[dict]:
    """Return [{'service': str, 'amount': str}, ...]. Empty list if no section."""
    if not notes:
        return []
    header = cfg["quote_parsing"].get("section_header", "future quotes")
    hdr_re = re.compile(re.escape(header).replace(r"\ ", r"\s+") + r"\s*[:\-–]?\s*", re.IGNORECASE)

    m = hdr_re.search(notes) or _HEADER_RE.search(notes)
    if not m:
        return []

    tail = notes[m.end():]
    # stop at a blank line - anything after a paragraph break is unrelated notes
    tail = re.split(r"\n\s*\n", tail, maxsplit=1)[0]
    # drop thousands separators so "1,200" doesn't split on its comma
    tail = re.sub(r"(?<=\d),(?=\d{3}\b)", "", tail)

    # split on commas OR newlines OR semicolons
    fragments = re.split(r"[,\n;]+", tail)
    out: list[dict] = []
    for frag in fragments:
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
            continue
        amount = amount.replace(",", "")
        if amount.endswith(".00"):
            amount = amount[:-3]
        out.append({"service": service, "amount": amount})
    return out


def render_quote_list(quotes: list[dict], cfg: Any) -> str:
    """Render quotes into the configured line_format, one per line."""
    qp = cfg["quote_parsing"]
    line_format = qp.get("line_format", "- [service]: $[amount]")
    titlecase = qp.get("titlecase_service", True)
    lines = []
    for q in quotes:
        service = _titlecase(q["service"]) if titlecase else q["service"]
        line = line_format.replace("[service]", service).replace("[amount]", q["amount"])
        lines.append(line)
    return "\n".join(lines)


def _titlecase(text: str) -> str:
    # "roof wash" -> "Roof Wash", but leave existing all-caps words alone
    return " ".join(w if w.isupper() else w.capitalize() for w in text.split())
