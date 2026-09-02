# Bluestone Pro Wash — Automated Post-Job Follow-Up

Day after a completed job, texts the customer a check-in. Reads the reply,
classifies it, and branches:

- **Satisfied** → closeout text: future-service quotes pulled from the tech's
  job notes, window-cleaning service plans (if windows were done), referral
  offer, review link.
- **Not satisfied** → stops all automation, texts Anderson to handle it.
- **Unclear** → one clarifying text, then escalate.

## Layout

| Path | What |
|---|---|
| `config.yml` | **Every tunable setting.** Timing, templates, discounts, numbers. |
| `bluestone/` | Decision engine (pure, offline-testable). |
| `bluestone/engine.py` | CLI the scheduled runner calls. |
| `RUNBOOK.md` | How a scheduled run drives the engine via RevDek/Quo. |
| `tests/run_tests.py` | `python3 tests/run_tests.py` — no deps beyond pyyaml. |
| `fixtures/sample_jobs.json` | Sample jobs incl. the "Dad Oneal" quote example. |

## Setup

```bash
python3 -m pip install --user pyyaml tzdata
```

## Try it (no messages sent)

```bash
python3 -m bluestone.engine preview --jobs fixtures/sample_jobs.json
python3 tests/run_tests.py
```

## Status

- Architecture: scheduled runner + RevDek/Quo integration (no standalone host).
- `completion_basis: marked_complete_at` — the poller records when a job first
  appears completed and sends 5h later, shifted into the 08:30–19:00 CT window.
- **`dry_run: true`** until stood up on a schedule and reviewed. See
  "Going live checklist" in `RUNBOOK.md`.
