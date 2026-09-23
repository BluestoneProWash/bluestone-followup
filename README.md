# Bluestone Pro Wash — Automated Post-Job Follow-Up

Day after a completed job, texts the customer a check-in. Reads the reply,
classifies it, and branches:

- **Satisfied** → after a short pause, a closeout text: future-service quotes
  from the tech's job notes, window-cleaning service plans (if windows were
  done), referral offer, review link.
- **Not satisfied** → stops, texts Anderson to handle it personally.
- **Wants a callback** ("call me") → stops, texts Anderson.
- **Unclear** (or anything else short of clearly satisfied) → stops, texts
  Anderson. No automated clarifying text - asking "did everything turn out
  well?" again after an ambiguous or negative reply reads as tone-deaf.
- **STOP** → opted out, never texted again.

**Manual override**: add the **"Skip to Closing Text"** indicator to a job in
RevDek (one tap) if you already know the customer is happy through some other
channel - e.g. they texted or called you directly. No check-in goes out; the
closeout goes out the next morning at the usual time, opening with "Hey
[name] thanks again for your business!" instead of "Great, glad to hear
it!" since there was no reply to react to.

**Credit card invoices**: if the tech marks the **"Payment Collected"**
indicator **Credit Card** (with the invoice link pasted on the line below,
e.g. `Credit Card · $300.00` then the link), the check-in text swaps to "Hey
[name] thank you for your business! Here's the invoice whenever you're
ready. How did everything turn out? [link]" - and it's sent **the moment
that run sees it, any hour of day**. This is the one exception to the
next-morning-only rule. If Credit Card is marked but the link isn't pasted
yet, the engine never sends a check-in without the promised link - instead
it texts Anderson once as a reminder to go paste it.

**Skip entirely**: add the **"Dont Follow Up"** indicator (either apostrophe
style) to a job and no check-in or closeout ever goes out. For cases like a
customer Anderson already reached out to directly whose satisfaction is
unknown, where asking for a review wouldn't be right.

**If the customer texts in first**: the check-in is written to sound like
Anderson personally texting them ("Hey [name] this is Anderson..."). If a
customer texts in before that check-in ever goes out - either it's a "Dont
Follow Up" job, or the check-in just hasn't fired yet - sending the canned
script over their unanswered message would read as him ignoring them. So the
engine never does that: it holds the check-in and texts Anderson instead
(once, same dedupe as any other alert) so he can reply personally. If a
check-in already went out before the situation came up, it's left alone -
they weren't ignored, so no alert.

## Design

Every run, the engine reads jobs and conversation threads from RevDek and
decides what to do. Idempotency (never send the same thing twice) comes from a
**"Bluestone Automation" job indicator** the automation stamps after every
send and checks before sending - not from the conversation, which only syncs
into RevDek when a human opens the inbox and so can't be trusted for that.

| Path | What |
|---|---|
| `config.yml` | Every tunable setting. Timing, templates, discounts. |
| `bluestone/` | The engine (pure functions). |
| `bluestone/state.py` | Reads reply/closeout state out of a conversation thread. |
| `bluestone/markers.py` | The "Bluestone Automation" job-indicator idempotency markers. |
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
- Check-in goes out at **9:00 AM CT the morning after the job**
  (`initial_followup.schedule: next_morning`). Switch to `hours_after_end` in
  config for "N hours after the scheduled end time" instead.
- `sending.job_allowlist` limits it to specific test job IDs. **An EMPTY list
  means no restriction — every completed job in RevDek is in scope.** Never
  leave it empty while testing; use a placeholder ID that matches nothing.
  Live-proven on a real customer 2026-09-16 and now running for every job.
- `sending.completed_since` is the other safety net: nothing dated before it
  is ever touched, no matter what `job_allowlist` says. Bumped forward
  whenever the allowlist opens up, so opening it never reaches back and texts
  a real customer whose job predates the decision to go live.
- `sending.halted: true` is a hard kill switch (send nothing, ever) independent
  of everything else. `sending.dry_run: true` logs without sending.
