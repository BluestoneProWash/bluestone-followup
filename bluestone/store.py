"""SQLite state + audit log.

Tables:
  job_followups        one row per job we've seen completed
  message_log          every outbound (and matched inbound) message
  classification_log   every classification decision + the text it was based on
  opt_outs             phone numbers that replied STOP
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

STATUS_SCHEDULED = "scheduled"
STATUS_AWAITING_REPLY = "awaiting_reply"
STATUS_CLOSEOUT_SCHEDULED = "closeout_scheduled"
STATUS_CLOSED_SATISFIED = "closed_satisfied"
STATUS_ESCALATED = "escalated"
STATUS_OPTED_OUT = "opted_out"
STATUS_ERROR = "error"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS job_followups (
    job_id            TEXT PRIMARY KEY,
    customer_id       TEXT,
    customer_phone    TEXT,
    customer_name     TEXT,
    first_name        TEXT,
    service_label     TEXT,
    date_label        TEXT,
    completed_seen_at TEXT NOT NULL,
    send_time         TEXT NOT NULL,
    checkin_sent_at   TEXT,
    closeout_due_at   TEXT,
    closeout_sent_at  TEXT,
    held_alert_sent_at TEXT,
    status            TEXT NOT NULL,
    unclear_retries   INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_followups_phone  ON job_followups(customer_phone);
CREATE INDEX IF NOT EXISTS idx_followups_status ON job_followups(status);

CREATE TABLE IF NOT EXISTS message_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT,
    direction   TEXT NOT NULL,          -- outbound | inbound
    stage       TEXT NOT NULL,          -- checkin | closeout | clarify | escalation | reply
    to_number   TEXT,
    body        TEXT NOT NULL,
    dry_run     INTEGER NOT NULL DEFAULT 0,
    provider_message_id TEXT,
    at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msglog_job ON message_log(job_id);

CREATE TABLE IF NOT EXISTS classification_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id        TEXT,
    reply_text    TEXT NOT NULL,
    classification TEXT NOT NULL,
    reason        TEXT,
    decided_by    TEXT,                 -- claude | rulebased
    at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS opt_outs (
    phone TEXT PRIMARY KEY,
    since TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_MIGRATIONS = [
    # (table, column, ddl) - added with ALTER TABLE if missing
    ("job_followups", "closeout_due_at", "ALTER TABLE job_followups ADD COLUMN closeout_due_at TEXT"),
    ("job_followups", "held_alert_sent_at", "ALTER TABLE job_followups ADD COLUMN held_alert_sent_at TEXT"),
]


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        self._migrate()
        self.db.commit()

    def _migrate(self) -> None:
        for table, column, ddl in _MIGRATIONS:
            cols = {r["name"] for r in self.db.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                self.db.execute(ddl)

    def close(self) -> None:
        self.db.close()

    # ---- followups -------------------------------------------------------
    def get_followup(self, job_id: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM job_followups WHERE job_id = ?", (job_id,)
        ).fetchone()

    def create_followup(self, job: dict, send_time: str, seen_at: str | None = None) -> None:
        now = _now()
        self.db.execute(
            """INSERT INTO job_followups
               (job_id, customer_id, customer_phone, customer_name, first_name,
                service_label, date_label, completed_seen_at, send_time, status,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                job["job_id"],
                job.get("customer_id"),
                job.get("customer_phone"),
                job.get("customer_name"),
                job.get("first_name"),
                job.get("service_label"),
                job.get("date_label"),
                seen_at or now,
                send_time,
                STATUS_SCHEDULED,
                now,
                now,
            ),
        )
        self.db.commit()

    def mark_checkin_sent(self, job_id: str, when: str | None = None) -> None:
        now = _now()
        self.db.execute(
            """UPDATE job_followups
               SET checkin_sent_at = ?, status = ?, updated_at = ?
               WHERE job_id = ?""",
            (when or now, STATUS_AWAITING_REPLY, now, job_id),
        )
        self.db.commit()

    def set_status(self, job_id: str, status: str) -> None:
        self.db.execute(
            "UPDATE job_followups SET status = ?, updated_at = ? WHERE job_id = ?",
            (status, _now(), job_id),
        )
        self.db.commit()

    def schedule_closeout(self, job_id: str, due_at: str) -> None:
        now = _now()
        self.db.execute(
            """UPDATE job_followups
               SET closeout_due_at = ?, status = ?, updated_at = ?
               WHERE job_id = ?""",
            (due_at, STATUS_CLOSEOUT_SCHEDULED, now, job_id),
        )
        self.db.commit()

    def due_closeouts(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM job_followups WHERE status = ? AND closeout_sent_at IS NULL",
            (STATUS_CLOSEOUT_SCHEDULED,),
        ).fetchall()

    def mark_held_alert_sent(self, job_id: str) -> None:
        self.db.execute(
            "UPDATE job_followups SET held_alert_sent_at = ?, updated_at = ? WHERE job_id = ?",
            (_now(), _now(), job_id),
        )
        self.db.commit()

    def mark_closeout_sent(self, job_id: str) -> None:
        now = _now()
        self.db.execute(
            """UPDATE job_followups
               SET closeout_sent_at = ?, status = ?, updated_at = ?
               WHERE job_id = ?""",
            (now, STATUS_CLOSED_SATISFIED, now, job_id),
        )
        self.db.commit()

    def bump_unclear(self, job_id: str) -> int:
        self.db.execute(
            "UPDATE job_followups SET unclear_retries = unclear_retries + 1, updated_at = ? WHERE job_id = ?",
            (_now(), job_id),
        )
        self.db.commit()
        row = self.get_followup(job_id)
        return int(row["unclear_retries"]) if row else 0

    def due_checkins(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM job_followups WHERE status = ? AND checkin_sent_at IS NULL",
            (STATUS_SCHEDULED,),
        ).fetchall()

    def awaiting_reply_by_phone(self, phone: str) -> sqlite3.Row | None:
        return self.db.execute(
            """SELECT * FROM job_followups
               WHERE customer_phone = ? AND status = ?
               ORDER BY checkin_sent_at DESC LIMIT 1""",
            (phone, STATUS_AWAITING_REPLY),
        ).fetchone()

    # ---- logs -----------------------------------------------------------
    def log_message(
        self,
        job_id: str | None,
        direction: str,
        stage: str,
        body: str,
        to_number: str | None = None,
        dry_run: bool = False,
        provider_message_id: str | None = None,
    ) -> None:
        self.db.execute(
            """INSERT INTO message_log
               (job_id, direction, stage, to_number, body, dry_run, provider_message_id, at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (job_id, direction, stage, to_number, body, int(dry_run), provider_message_id, _now()),
        )
        self.db.commit()

    def log_classification(
        self, job_id: str | None, reply_text: str, classification: str, reason: str, decided_by: str
    ) -> None:
        self.db.execute(
            """INSERT INTO classification_log
               (job_id, reply_text, classification, reason, decided_by, at)
               VALUES (?,?,?,?,?,?)""",
            (job_id, reply_text, classification, reason, decided_by, _now()),
        )
        self.db.commit()

    def already_logged_inbound(self, job_id: str, body: str) -> bool:
        row = self.db.execute(
            """SELECT 1 FROM message_log
               WHERE job_id = ? AND direction = 'inbound' AND body = ? LIMIT 1""",
            (job_id, body),
        ).fetchone()
        return row is not None

    # ---- opt-outs ------------------------------------------------------
    def is_opted_out(self, phone: str) -> bool:
        return self.db.execute(
            "SELECT 1 FROM opt_outs WHERE phone = ?", (phone,)
        ).fetchone() is not None

    def add_opt_out(self, phone: str) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO opt_outs (phone, since) VALUES (?, ?)", (phone, _now())
        )
        self.db.commit()

    # ---- reporting ---------------------------------------------------
    def snapshot(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT status, COUNT(*) n FROM job_followups GROUP BY status"
        ).fetchall()
        return [dict(r) for r in rows]
