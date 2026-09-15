"""Durable per-job idempotency markers, stored in a RevDek job indicator.

The conversation history (Quo -> RevDek) only syncs when a human opens the
RevDek inbox, so the automation can't trust it to know what it already sent.
Job indicators sync reliably even headless, so we record every send there.

The marker indicator's `notes` field holds one line per stage:

    checkin sent 2026-09-10T14:03:00Z
    closeout sent 2026-09-10T15:20:00Z
    escalated 2026-09-10T15:05:00Z

`stage` is the first token of each line: checkin | closeout | escalated

Note: a job followed up before 2026-09-15 may still carry an old "clarify sent
..." line from the since-removed clarifying-text step. That token is simply
unrecognized now and ignored - harmless.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

STAGES = ("checkin", "closeout", "escalated")


def indicator_name(cfg: Any) -> str:
    return (cfg.get("markers", {}) or {}).get("indicator_name", "Bluestone Automation")


def job_marker_note(job: dict, cfg: Any) -> str | None:
    """The marker indicator's current note text on a normalized job, or None.

    Defensive: if the job somehow has more than one entry for the marker
    indicator (shouldn't happen, but a duplicate add_job_indicator call could
    do it), merge every stage found across ALL of them rather than reading
    only the first - a stage marked on any entry counts as sent.
    """
    want = indicator_name(cfg).strip().lower()
    notes = [ind.get("notes") for ind in (job.get("indicators") or [])
             if (ind.get("name") or "").strip().lower() == want and ind.get("notes")]
    if not notes:
        return None
    if len(notes) == 1:
        return notes[0]
    return "\n".join(notes)


def parse(note: str | None) -> set[str]:
    """Return the set of stages already recorded in a marker note."""
    out: set[str] = set()
    for line in (note or "").splitlines():
        tok = line.strip().split(" ", 1)[0].lower()
        if tok in STAGES:
            out.add(tok)
    return out


def has(note: str | None, stage: str) -> bool:
    return stage in parse(note)


def add(note: str | None, stage: str, at: datetime) -> str:
    """Return the note with `stage` recorded. Idempotent - no duplicate lines."""
    if has(note, stage):
        return (note or "").strip()
    aware = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    stamp = aware.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    verb = "sent " if stage in ("checkin", "closeout", "clarify") else ""
    line = f"{stage} {verb}{stamp}"
    existing = (note or "").strip()
    return f"{existing}\n{line}" if existing else line
