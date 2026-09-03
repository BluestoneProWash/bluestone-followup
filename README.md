# Bluestone Pro Wash — Automated Post-Job Follow-Up

Day after a completed job, texts the customer a check-in. Reads the reply,
classifies it, and branches:

- **Satisfied** → after a short pause, a closeout text: future-service quotes
  from the tech's job notes, window-cleaning service plans (if windows were
  done), referral offer, review link.
- **Not satisfied** → stops, texts Anderson to handle it personally.
- **Wants a callback** ("call me") → stops, texts Anderson.
- **Unclear** → one clarifying text, then escalate.
- **STOP** → opted out, never texted again.

## Design

**Stateless.** No database. Every run, the engine reads the RevDek conversation
threads and works out what to do from what's already been said. That means the
cloud runner only needs to *read* the repo and RevDek — nothing to persist.

| Path | What |
|---|---|
| `config.yml` | Every tunable setting. Timing, templates, discounts. |
| `bluestone/` | The engine (pure functions). |
| `bluestone/state.py` | Reads follow-up state out of a conversation thread. |
| `bluestone/pipeline.py` | `plan(jobs, threads, now, cfg)` → list of actions. |
| `bluestone/engine.py` | CLI: `preview`, `plan`, `status`. |
| `CLOUD_RUNNER.md` | What the hourly cloud routine does. |
| `tests/run_tests.py` | `python3 tests/run_tests.py` (needs `pyyaml`, `tzdata`). |

## Secrets

Anderson's escalation cell/email are **not** in this repo. They come from env
vars `BLUESTONE_ESCALATION_SMS` / `BLUESTONE_ESCALATION_EMAIL`, set on the cloud
routine. Locally: `export BLUESTONE_ESCALATION_SMS=+1...` before running.

## Try it (no messages sent)

```bash
python3 -m pip install --user pyyaml tzdata
export BLUESTONE_ESCALATION_SMS=+15555550000
python3 -m bluestone.engine preview --jobs fixtures/sample_jobs.json
python3 tests/run_tests.py
```

## Status

- Runs as an hourly **cloud routine** on Anthropic's infrastructure — the
  business's computers can be off.
- `completion_basis: scheduled_end_time` — check-in goes 5h after the job's
  scheduled end, shifted into 08:30–19:00 CT.
- **`dry_run: true`** until reviewed. `sending.job_allowlist` limits it to
  specific test jobs; empty the list to go live for all jobs.
