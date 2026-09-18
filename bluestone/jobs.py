"""Normalize a raw RevDek job (+ customer contact) into the shape the engine uses."""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

_URL_RE = re.compile(r"https?://\S+")


def _name_case(name: str) -> str:
    # "anderson" -> "Anderson", but leave "McRae" / "JD" as typed
    out = []
    for w in name.split():
        out.append(w.capitalize() if w.islower() else w)
    return " ".join(out)


def _closing_quotes_note(raw: dict) -> str | None:
    """The 'Closing Quotes Given' indicator's note text on a raw RevDek job."""
    for ind in raw.get("indicators") or []:
        if "closing quotes" in (ind.get("name") or "").lower():
            return ind.get("notes")
    return None


def _has_force_satisfied(raw: dict) -> bool:
    """The 'Skip to Closing Text' indicator - a manual override Anderson can add
    in RevDek (same one-tap pattern as 'Closing Quotes Given') to skip the
    automated check-in/reply and go straight to the closeout text."""
    for ind in raw.get("indicators") or []:
        if "skip to closing" in (ind.get("name") or "").lower():
            return True
    return False


def _payment_note(raw: dict) -> str | None:
    for ind in raw.get("indicators") or []:
        if "payment collected" in (ind.get("name") or "").lower():
            return ind.get("notes")
    return None


def _needs_invoice(raw: dict) -> bool:
    """Tech marked payment as 'Credit Card' on the 'Payment Collected' indicator -
    this job needs an invoice link in its check-in text before anything else
    can go out."""
    note = _payment_note(raw) or ""
    return "credit card" in note.lower()


def _invoice_link(raw: dict) -> str | None:
    """The payment link Anderson pastes below the 'Credit Card' line once he's
    created the invoice in RevDek. None if not pasted yet."""
    note = _payment_note(raw) or ""
    if "credit card" not in note.lower():
        return None
    m = _URL_RE.search(note)
    return m.group(0) if m else None


def _service_label(service_type: list[str]) -> str:
    st = [s.strip() for s in (service_type or []) if s and s.strip()]
    if not st:
        return "service"
    label = " and ".join(st)
    return label.lower()


def _date_label(raw_date: str | None) -> str:
    if not raw_date:
        return ""
    try:
        d = datetime.strptime(raw_date[:10], "%Y-%m-%d").date()
    except ValueError:
        return raw_date
    return f"{d.strftime('%b')} {d.day}"


def _full_name(customer: dict | None, job_customer: dict | None) -> str:
    c = customer or job_customer or {}
    first = (c.get("first_name") or "").strip()
    last = (c.get("last_name") or "").strip()
    return _name_case(" ".join(p for p in (first, last) if p))


def _phone(customer: dict | None) -> str | None:
    if not customer:
        return None
    for key in ("phone", "phone_number", "mobile", "cell", "primary_phone"):
        v = customer.get(key)
        if v:
            return normalize_phone(v)
    # some APIs nest under contact/phones
    phones = customer.get("phones")
    if isinstance(phones, list) and phones:
        first = phones[0]
        if isinstance(first, dict):
            return normalize_phone(first.get("number") or first.get("value"))
        return normalize_phone(first)
    return None


def normalize_phone(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    if str(raw).startswith("+"):
        return str(raw)
    return "+" + digits if digits else None


def normalize_job(raw: dict, customer: dict | None = None) -> dict:
    job_customer = raw.get("customer") or {}
    first = (
        (customer or {}).get("first_name")
        or job_customer.get("first_name")
        or "there"
    ).strip() or "there"
    first = _name_case(first)
    return {
        "job_id": raw.get("id") or raw.get("job_id"),
        "customer_id": raw.get("customer_id"),
        "first_name": first,
        "customer_name": _full_name(customer, job_customer),
        "customer_phone": _phone(customer),
        "service_type": raw.get("service_type", []),
        "service_label": _service_label(raw.get("service_type", [])),
        "price": raw.get("price"),
        "notes": raw.get("notes"),
        "closing_quotes_note": _closing_quotes_note(raw),
        "force_satisfied": _has_force_satisfied(raw),
        "needs_invoice": _needs_invoice(raw),
        "invoice_link": _invoice_link(raw),
        "indicators": raw.get("indicators") or [],
        "date": raw.get("date"),
        "time": raw.get("time"),
        "end_time": raw.get("end_time"),
        "date_label": _date_label(raw.get("date")),
        "completed": bool(raw.get("completed")),
    }
