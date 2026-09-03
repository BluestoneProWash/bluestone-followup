"""When to send the day-after check-in.

completion time + delay_hours_after_completion, then shifted into the daily
send window:
  - lands after send_window_end  -> deferred_send_time the NEXT day
  - lands before send_window_start -> send_window_start the SAME day
  - otherwise -> as-is
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore


def tz(cfg: Any):
    name = cfg["timezone"]
    if ZoneInfo is None:
        raise RuntimeError("zoneinfo unavailable; install tzdata")
    return ZoneInfo(name)


def _parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def now_ct(cfg: Any) -> datetime:
    return datetime.now(tz(cfg))


def to_ct(dt: datetime, cfg: Any) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz(cfg))
    return dt.astimezone(tz(cfg))


def job_completion_time(job: dict, cfg: Any) -> datetime | None:
    """The moment a job counts as 'complete', per config.initial_followup.completion_basis.

    scheduled_end_time (default, stateless): the job's end_time on its date.
    Returns a CT-aware datetime, or None if the job lacks a usable date.
    """
    basis = cfg["initial_followup"].get("completion_basis", "scheduled_end_time")
    d = (job.get("date") or "")[:10]
    if not d:
        return None
    end = (job.get("end_time") or job.get("time") or "17:00")[:8]
    try:
        y, m, day = (int(x) for x in d.split("-"))
        hh, mm = _parse_hhmm(end[:5]).hour, _parse_hhmm(end[:5]).minute
        return datetime(y, m, day, hh, mm, tzinfo=tz(cfg))
    except (ValueError, TypeError):
        return None


def checkin_due_time(job: dict, cfg: Any) -> datetime | None:
    ct = job_completion_time(job, cfg)
    if ct is None:
        return None
    return compute_send_time(ct, cfg)


def compute_send_time(completed_at: datetime, cfg: Any) -> datetime:
    fu = cfg["initial_followup"]
    completed_at = to_ct(completed_at, cfg)
    delay = timedelta(hours=float(fu.get("delay_hours_after_completion", 5)))
    target = completed_at + delay

    win_start = _parse_hhmm(fu.get("send_window_start", "08:30"))
    win_end = _parse_hhmm(fu.get("send_window_end", "19:00"))
    deferred = _parse_hhmm(fu.get("deferred_send_time", "08:30"))

    if target.time() > win_end:
        nxt = target.date() + timedelta(days=1)
        return datetime.combine(nxt, deferred, tzinfo=tz(cfg))
    if target.time() < win_start:
        return datetime.combine(target.date(), win_start, tzinfo=tz(cfg))
    return target


def is_due(send_time: datetime, now: datetime, cfg: Any) -> bool:
    return to_ct(now, cfg) >= to_ct(send_time, cfg)
