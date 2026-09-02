"""CLI wrapper the scheduled runner calls. See RUNBOOK.md.

    python -m bluestone.engine preview   --jobs jobs.json
    python -m bluestone.engine checkins  --jobs jobs.json [--now ISO]
    python -m bluestone.engine apply     --results results.json
    python -m bluestone.engine reply     --jobs jobs.json --phone +1205... --text "..." [--classification SATISFIED]
    python -m bluestone.engine status

`jobs.json` is a list of RAW RevDek job objects, optionally each with a
"customer" object carrying phone/name (merge it in before calling).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from . import pipeline
from .config import load_config, unfilled_placeholders
from .jobs import normalize_job, normalize_phone
from .timing import now_ct, to_ct
from .store import Store


def _load_jobs(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    if isinstance(raw, dict) and "jobs" in raw:
        raw = raw["jobs"]
    return [normalize_job(j, j.get("customer_full") or j.get("customer_contact")) for j in raw]


def _cfg_and_store(args):
    cfg = load_config(args.config)
    store = Store(cfg["database"]["path"])
    return cfg, store


def cmd_preview(args) -> int:
    from . import templates
    cfg = load_config(args.config)
    jobs = _load_jobs(args.jobs)
    ph = unfilled_placeholders(cfg)
    if ph:
        print(f"!! config still has PLACEHOLDER values: {', '.join(ph)}\n", file=sys.stderr)
    for job in jobs:
        print("=" * 70)
        print(f"job {job['job_id']}  |  {job['customer_name'] or job['first_name']}  "
              f"|  {', '.join(job['service_type'] or [])}  |  ${job.get('price')}")
        print(f"notes: {job.get('notes')!r}")
        print("-" * 70)
        print("[CHECK-IN]")
        print(templates.render_check_in(job, cfg))
        print()
        co = templates.render_closeout(job, cfg)
        print(f"[CLOSEOUT if SATISFIED]  quotes={co['has_quotes']}  window_block={co['has_window_block']}")
        print(co["body"])
        print()
    return 0


def cmd_checkins(args) -> int:
    cfg, store = _cfg_and_store(args)
    jobs = _load_jobs(args.jobs)
    now = to_ct(datetime.fromisoformat(args.now), cfg) if args.now else now_ct(cfg)
    actions = pipeline.plan_checkins(jobs, now, cfg, store)
    _emit(actions, cfg)
    return 0


def cmd_reply(args) -> int:
    cfg, store = _cfg_and_store(args)
    jobs = _load_jobs(args.jobs)
    phone = normalize_phone(args.phone)
    job = None
    if args.job_id:
        job = next((j for j in jobs if j["job_id"] == args.job_id), None)
    if job is None and phone:
        row = store.awaiting_reply_by_phone(phone)
        if row:
            job = next((j for j in jobs if j["job_id"] == row["job_id"]), None)
            if job is None:
                job = {
                    "job_id": row["job_id"],
                    "customer_phone": row["customer_phone"],
                    "customer_name": row["customer_name"],
                    "first_name": row["first_name"],
                    "service_label": row["service_label"],
                    "date_label": row["date_label"],
                    "service_type": [],
                    "price": None,
                    "notes": None,
                }
    if job is None:
        print(json.dumps({"error": "no matching awaiting_reply thread", "phone": phone}))
        return 1
    if phone and not job.get("customer_phone"):
        job["customer_phone"] = phone
    actions = pipeline.handle_reply(
        job, args.text, now_ct(cfg), cfg, store, classification=args.classification
    )
    _emit(actions, cfg)
    return 0


def cmd_closeouts(args) -> int:
    cfg, store = _cfg_and_store(args)
    jobs = _load_jobs(args.jobs)
    now = to_ct(datetime.fromisoformat(args.now), cfg) if args.now else now_ct(cfg)
    actions = pipeline.plan_closeouts(jobs, now, cfg, store)
    _emit(actions, cfg)
    return 0


def cmd_apply(args) -> int:
    cfg, store = _cfg_and_store(args)
    with open(args.results, encoding="utf-8") as fh:
        results = json.load(fh)
    for r in results:
        stage = r["stage"]
        job_id = r.get("job_id")
        body = r.get("body", "")
        to = r.get("to")
        pmid = r.get("provider_message_id")
        if stage == "checkin":
            pipeline.apply_checkin_sent(job_id, body, to, cfg, store, pmid)
        elif stage == "closeout":
            pipeline.apply_closeout_sent(job_id, body, to, cfg, store, pmid)
        elif stage == "clarify":
            pipeline.apply_clarify_sent(job_id, body, to, cfg, store)
        elif stage == "escalation":
            pipeline.apply_escalation_sent(job_id, body, to, cfg, store)
        elif stage == "notes_needed":
            pipeline.apply_notes_needed_alert_sent(job_id, body, to, cfg, store)
    print(json.dumps({"applied": len(results)}))
    return 0


def cmd_status(args) -> int:
    cfg, store = _cfg_and_store(args)
    print(json.dumps({
        "dry_run": cfg["sending"].get("dry_run", True),
        "a2p_registered": cfg["sending"].get("a2p_10dlc_registered", False),
        "unfilled_placeholders": unfilled_placeholders(cfg),
        "followups_by_status": store.snapshot(),
    }, indent=2))
    return 0


def _emit(actions, cfg) -> None:
    dry = cfg["sending"].get("dry_run", True)
    print(json.dumps({
        "dry_run": dry,
        "count": len(actions),
        "actions": [a.as_dict() for a in actions],
    }, indent=2))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="bluestone.engine")
    p.add_argument("--config", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("preview"); sp.add_argument("--jobs", required=True); sp.set_defaults(fn=cmd_preview)
    sp = sub.add_parser("checkins"); sp.add_argument("--jobs", required=True); sp.add_argument("--now"); sp.set_defaults(fn=cmd_checkins)
    sp = sub.add_parser("closeouts"); sp.add_argument("--jobs", required=True); sp.add_argument("--now"); sp.set_defaults(fn=cmd_closeouts)
    sp = sub.add_parser("reply")
    sp.add_argument("--jobs", required=True); sp.add_argument("--job-id"); sp.add_argument("--phone")
    sp.add_argument("--text", required=True); sp.add_argument("--classification")
    sp.set_defaults(fn=cmd_reply)
    sp = sub.add_parser("apply"); sp.add_argument("--results", required=True); sp.set_defaults(fn=cmd_apply)
    sp = sub.add_parser("status"); sp.set_defaults(fn=cmd_status)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
