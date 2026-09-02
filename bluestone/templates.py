"""Fill [bracket] placeholders in the config templates and tidy whitespace."""
from __future__ import annotations

import re
from typing import Any

from . import quotes as quotes_mod
from . import window_plans


def _fill(text: str, values: dict[str, str]) -> str:
    def repl(m: re.Match) -> str:
        key = m.group(1)
        return str(values.get(key, m.group(0)))

    return re.sub(r"\[([a-z0-9_]+)\]", repl, text)


def _tidy(text: str) -> str:
    # collapse 3+ newlines (left by an empty block) to a single blank line
    text = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", text)
    # strip trailing spaces on each line
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return text.strip()


def base_values(cfg: Any) -> dict[str, str]:
    tv = cfg["template_values"]
    return {
        "quote_validity_months": str(tv.get("quote_validity_months", 12)),
        "referral_discount": str(tv.get("referral_discount", "$50")),
        "review_link": str(tv.get("review_link", "")),
        "business_name": str(tv.get("business_name", "Bluestone Pro Wash")),
    }


def render_check_in(job: dict, cfg: Any) -> str:
    values = base_values(cfg)
    values.update(
        {
            "first_name": job.get("first_name", "there").strip() or "there",
            "service": job.get("service_label", ""),
            "date": job.get("date_label", ""),
        }
    )
    return _tidy(_fill(cfg["templates"]["check_in"], values))


def render_closeout(job: dict, cfg: Any) -> dict:
    """Return {'body': str, 'has_quotes': bool, 'has_window_block': bool}."""
    notes = job.get("notes")
    parsed = quotes_mod.parse_future_quotes(notes, cfg)
    quote_list = quotes_mod.render_quote_list(parsed, cfg) if parsed else ""

    window_block = window_plans.render_window_block(
        job.get("service_type", []), job.get("price"), cfg
    )

    values = base_values(cfg)
    values.update({"quote_list": quote_list, "window_plan_block": window_block})

    if parsed:
        template = cfg["templates"]["closeout_with_quotes"]
    else:
        template = cfg["templates"]["closeout_no_quotes"]

    body = _tidy(_fill(template, values))
    return {
        "body": body,
        "has_quotes": bool(parsed),
        "has_window_block": bool(window_block),
        "parsed_quotes": parsed,
    }


def render_unclear(cfg: Any) -> str:
    return _tidy(_fill(cfg["templates"]["unclear_clarify"], base_values(cfg)))


def render_contact_request(job: dict, reply_text: str, cfg: Any) -> str:
    values = base_values(cfg)
    values.update(
        {
            "customer_name": (job.get("customer_name") or "").strip(),
            "customer_phone": job.get("customer_phone", ""),
            "service": job.get("service_label", ""),
            "date": job.get("date_label", ""),
            "reply_text": (reply_text or "").strip(),
        }
    )
    return _tidy(_fill(cfg["templates"]["contact_request"], values))


def render_notes_needed(job: dict, cfg: Any) -> str:
    values = base_values(cfg)
    values.update(
        {
            "customer_name": (job.get("customer_name") or "").strip(),
            "service": job.get("service_label", ""),
            "date": job.get("date_label", ""),
        }
    )
    return _tidy(_fill(cfg["templates"]["notes_needed"], values))


def render_escalation(job: dict, reply_text: str, cfg: Any) -> str:
    values = base_values(cfg)
    values.update(
        {
            "customer_name": job.get("customer_name", "").strip(),
            "customer_phone": job.get("customer_phone", ""),
            "service": job.get("service_label", ""),
            "date": job.get("date_label", ""),
            "reply_text": (reply_text or "").strip(),
        }
    )
    return _tidy(_fill(cfg["templates"]["escalation"], values))
