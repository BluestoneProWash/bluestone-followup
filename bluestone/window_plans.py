"""Window-cleaning service-plan paragraph for the closeout text.

Rules (from Anderson):
  - If the job's service list is window cleaning bundled WITH another service,
    we can't isolate the window price -> state percentages only.
  - If the job's ONLY service is window cleaning -> show the real plan price
    per visit, computed off this job's price.
"""
from __future__ import annotations

from typing import Any


def _matches(service: str, needle: str) -> bool:
    return needle.lower() in service.lower()


def job_has_window_cleaning(service_types: list[str], cfg: Any) -> bool:
    needle = cfg["window_cleaning_plans"].get("service_match", "window cleaning")
    return any(_matches(s, needle) for s in (service_types or []))


def job_is_window_cleaning_only(service_types: list[str], cfg: Any) -> bool:
    needle = cfg["window_cleaning_plans"].get("service_match", "window cleaning")
    st = [s for s in (service_types or []) if s and s.strip()]
    return bool(st) and all(_matches(s, needle) for s in st)


def _round(amount: float, mode: str) -> int | float:
    if mode == "nearest_5":
        return int(round(amount / 5.0) * 5)
    if mode == "none":
        return round(amount, 2)
    return int(round(amount))  # nearest_dollar


def render_window_block(service_types: list[str], job_price: float | None, cfg: Any) -> str:
    """Return the paragraph, or '' when it shouldn't appear."""
    wp = cfg["window_cleaning_plans"]
    if not wp.get("enabled", True):
        return ""
    if not job_has_window_cleaning(service_types, cfg):
        return ""

    semi_pct = wp.get("semi_annual_discount_pct", 15)
    qtr_pct = wp.get("quarterly_discount_pct", 20)
    templates = cfg["templates"]

    want_price = (
        wp.get("show_calculated_price", False)
        and job_is_window_cleaning_only(service_types, cfg)
        and job_price is not None
        and float(job_price) > 0
    )

    if want_price:
        rounding = wp.get("price_rounding", "nearest_dollar")
        semi_price = _round(float(job_price) * (1 - semi_pct / 100.0), rounding)
        qtr_price = _round(float(job_price) * (1 - qtr_pct / 100.0), rounding)
        body = templates["window_plan_block_priced"]
        return (
            body.replace("[semi_annual_pct]", str(semi_pct))
            .replace("[quarterly_pct]", str(qtr_pct))
            .replace("[semi_annual_price]", str(semi_price))
            .replace("[quarterly_price]", str(qtr_price))
            .strip()
        )

    body = templates["window_plan_block"]
    return (
        body.replace("[semi_annual_pct]", str(semi_pct))
        .replace("[quarterly_pct]", str(qtr_pct))
        .strip()
    )
