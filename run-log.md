# Run log

The cloud routine appends a short entry per run here isn't needed — it reports
in its session summary instead. This file is a manual changelog.

## 2026-09-02 — build + manual testing

- Engine built and tested end-to-end (dry-run + real sends to the owner's own
  phone) against real RevDek test jobs:
  - windows-only job → closeout with per-visit priced window plan ✓
  - windows + another service → closeout with percentage window plan ✓
  - no future quotes, no windows → closeout with referral + review only ✓
  - satisfied classification on mixed-tone replies ✓
- Added: 2-minute delay before the closeout; CONTACT_REQUEST bucket
  ("call me" → straight to Anderson, no auto-reply); name title-casing;
  `require_future_quotes` (default off — send the rest even with no quotes).

## 2026-09-03 — moved to stateless cloud routine

- Reworked the engine to keep **no database** — every run it reads the RevDek
  conversation threads and derives state from them. This let the whole thing
  move to an hourly **cloud routine** that runs on Anthropic's infrastructure
  regardless of whether the owner's computer is on.
- `completion_basis` switched to `scheduled_end_time` (no state to record the
  marked-complete moment).
- Escalation contact details moved to env vars (`BLUESTONE_ESCALATION_SMS`).
- Still `dry_run: true`; `sending.job_allowlist` scopes it to test jobs.
