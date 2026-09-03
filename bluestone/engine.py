"""CLI the cloud runner calls. Stateless - see CLOUD_RUNNER.md.

    python3 -m bluestone.engine preview --jobs jobs.json
    python3 -m bluestone.engine plan    --jobs jobs.json --threads threads.json [--now ISO]
    python3 -m bluestone.engine status

jobs.json    : list of raw RevDek job objects, each optionally with
               "customer_full": {"first_name","last_name","phone"}
threads.json : {"<phone>": [{"direction":"inbound"|"outbound","text":"...",
               "at":"<ISO8601>"}, ...], ...}  - one entry per in-scope customer
               phone AND one for the escalation number.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from . import pipeline, templates
from .config import load_config, unfilled_placeholders
from .jobs import normalize_job, normalize_phone
from .timing import now_ct, to_ct


def _parse_dt(s: str) -> datetime:
    s = s.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _load_jobs(path: str) -> list[dict]:
    raw = json.load(open(path, encoding="utf-8"))
    if isinstance(raw, dict) and "jobs" in raw:
        raw = raw["jobs"]
    return [normalize_job(j, j.get("customer_full") or j.get("customer_contact")) for j in raw]


def _load_threads(path: str) -> dict[str, list[dict]]:
    raw = json.load(open(path, encoding="utf-8"))
    out: dict[str, list[dict]] = {}
    for phone, msgs in raw.items():
        key = normalize_phone(phone) or phone
        out[key] = [
            {"direction": m["direction"], "text": m.get("text", ""), "at": _parse_dt(m["at"])}
            for m in msgs
        ]
    return out


def cmd_preview(args) -> int:
    cfg = load_config(args.config)
    ph = unfilled_placeholders(cfg)
    if ph:
        print(f"!! config still has PLACEHOLDER values: {', '.join(ph)}\n", file=sys.stderr)
    for job in _load_jobs(args.jobs):
        print("=" * 70)
        print(f"job {job['job_id']}  |  {job['customer_name'] or job['first_name']}  |  "
              f"{', '.join(job['service_type'] or [])}  |  ${job.get('price')}")
        print(f"notes: {job.get('notes')!r}")
        print("-" * 70)
        print("[CHECK-IN]\n" + templates.render_check_in(job, cfg) + "\n")
        co = templates.render_closeout(job, cfg)
        print(f"[CLOSEOUT if SATISFIED]  quotes={co['has_quotes']}  window={co['has_window_block']}")
        print(co["body"] + "\n")
    return 0


def cmd_plan(args) -> int:
    cfg = load_config(args.config)
    jobs = _load_jobs(args.jobs)
    threads = _load_threads(args.threads)
    now = to_ct(_parse_dt(args.now), cfg) if args.now else now_ct(cfg)
    actions = pipeline.plan(jobs, threads, now, cfg)
    print(json.dumps({
        "dry_run": cfg["sending"].get("dry_run", True),
        "now": now.isoformat(),
        "count": len(actions),
        "actions": [a.as_dict() for a in actions],
    }, indent=2))
    return 0


def cmd_status(args) -> int:
    cfg = load_config(args.config)
    print(json.dumps({
        "dry_run": cfg["sending"].get("dry_run", True),
        "a2p_registered": cfg["sending"].get("a2p_10dlc_registered", False),
        "completion_basis": cfg["initial_followup"].get("completion_basis"),
        "job_allowlist": cfg["sending"].get("job_allowlist") or [],
        "unfilled_placeholders": unfilled_placeholders(cfg),
    }, indent=2))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="bluestone.engine")
    p.add_argument("--config", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("preview"); sp.add_argument("--jobs", required=True); sp.set_defaults(fn=cmd_preview)
    sp = sub.add_parser("plan"); sp.add_argument("--jobs", required=True)
    sp.add_argument("--threads", required=True); sp.add_argument("--now")
    sp.set_defaults(fn=cmd_plan)
    sp = sub.add_parser("status"); sp.set_defaults(fn=cmd_status)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
