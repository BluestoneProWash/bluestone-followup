# Cloud runner playbook

What the scheduled cloud routine does every hour. The repo is your working
directory. You have the **RevDek connector**.

Tool names (server prefix varies — match by name): `search_jobs`, `get_job`,
`get_job_indicators`, `add_job_indicator`, `update_job_indicator_notes`,
`list_indicators`, `search_customers`, `search_customer_conversations`,
`get_customer_timeline`, `get_conversation_messages`, `list_recent_conversations`,
`send_customer_message`.

**Every outbound text: `send_customer_message(to=..., text=..., provider="quo")`.**
Never `send_sms`.

**How the automation avoids double-texting:** the Quo conversation only syncs into
RevDek when a human opens the inbox, so it can't be trusted. Instead, after every
send you stamp a **"Bluestone Automation"** job indicator, and the engine checks
that indicator before deciding to send. **Writing that marker after a send is not
optional — skip it and the next run double-texts.**

---

## 0. Setup

Each shell command runs in a fresh shell, so pass the config env vars **inline
on every `bluestone.engine` call**:

```
export BSENV="BLUESTONE_ESCALATION_SMS=$BLUESTONE_ESCALATION_SMS BLUESTONE_FROM_NUMBER=$BLUESTONE_FROM_NUMBER"
python3 -m pip install --quiet --user pyyaml tzdata 2>/dev/null || true
env $BSENV python3 -m bluestone.engine status
```

(`BLUESTONE_ESCALATION_SMS` / `BLUESTONE_FROM_NUMBER` are provided to the run.
`BSENV` won't survive into the next shell either — just retype the
`env BLUESTONE_ESCALATION_SMS=... BLUESTONE_FROM_NUMBER=...` prefix each time, or
re-export `BSENV` at the top of each command.)

If `plan` output contains a `{"stage": "halted"}` note, the kill switch is on:
**send nothing, do nothing, just report "halted" and stop.**

If it contains a `{"stage": "config_error"}` note, the escalation contact env var
wasn't set on this command (check you used the `env BLUESTONE_ESCALATION_SMS=...`
prefix). **Send nothing.** Fix the command and it should resolve next run.

**Marker indicator preflight:** call `list_indicators` and confirm a
**"Bluestone Automation"** indicator exists and is enabled. If it is missing or
disabled, **STOP the run — send nothing** — and report that the marker indicator
needs to be created/enabled in RevDek. Without it the automation cannot record
what it sent and would double-text.

`status` echoes `dry_run` and `job_allowlist`. **`status` is the authority on
`dry_run` — not any hint in your prompt.**
- `dry_run: true`  → run `plan`, send NOTHING, just write the summary.
- `dry_run: false` → run `plan` and **actually send** every `send_sms` /
  `notify_anderson` action with `send_customer_message`. A `false` value is
  intentional, never a mistake to be second-guessed.

## 1. Gather jobs

1. `search_jobs(completed=true, limit=50)`.
2. If `status` showed a non-empty `job_allowlist`, drop every job not on it.
3. Also drop jobs whose `date` is more than ~2 weeks old.
4. For each remaining job:
   - `get_job(job_id)` for the full `notes`.
   - `get_job_indicators(job_id)` — holds both the **"Closing Quotes Given"**
     indicator (quotes) and the **"Bluestone Automation"** indicator (send markers).
   - Find the customer's phone (`search_customers` on the name / `customer_id`).
5. Write `jobs.json`: a JSON list of the raw job objects, each with:
   - `"customer_full": {"first_name","last_name","phone"}`
   - `"indicators": [ ... ]` — the **full array from `get_job_indicators`**
     (not the possibly-truncated one from `search_jobs`). The engine reads
     "Closing Quotes Given" for quotes and "Bluestone Automation" for markers.

## 2. Gather threads

For every phone in `jobs.json` **plus the escalation number**
(`BLUESTONE_ESCALATION_SMS`), pull that conversation
(`search_customer_conversations` / `get_conversation_messages`, ~20 msgs).

Write `threads.json`:

```json
{
  "+1205...": [
    {"direction": "outbound", "text": "...", "at": "2026-09-02T15:00:00Z"},
    {"direction": "inbound",  "text": "...", "at": "2026-09-02T15:06:00Z"}
  ],
  "+1205...": [ ... ]
}
```

`direction` is from the customer's side: **inbound** = from the customer,
**outbound** = from us. `at` is the message timestamp (any ISO 8601).

## 3. Decide

```
env BLUESTONE_ESCALATION_SMS=$BLUESTONE_ESCALATION_SMS BLUESTONE_FROM_NUMBER=$BLUESTONE_FROM_NUMBER \
  python3 -m bluestone.engine plan --jobs jobs.json --threads threads.json
```

Prints `{"dry_run":..., "actions":[...]}`. Each action:

| kind | stage | meaning |
|---|---|---|
| `send_sms` | `checkin` / `closeout` | send `body` to `to` (the customer) |
| `notify_anderson` | `escalation` / `closeout_reply` | send `body` to `to` (the escalation number) — **NOT the customer** |
| `note` | anything | informational — do nothing, no marker |

A job carrying the **"Confirmed Satisfied"** indicator (Anderson's manual
override - he already knows the customer is happy through some other channel)
skips straight to a `closeout` action, no check-in or reply needed. This is
handled entirely inside the engine from the `indicators` array you already
wrote to `jobs.json` in step 1 — nothing extra to do here.

## 4. Execute each action (only if `dry_run` is false)

For every `send_sms` / `notify_anderson` action, **in this exact order**:

1. `send_customer_message(to=<action.to>, text=<action.body>, provider="quo")`
2. Confirm it returned success.
3. **Immediately** write the marker to the job's "Bluestone Automation" indicator:
   - The action gives you `marker_note_after` (the exact full note text) and
     `marker_indicator` (`"Bluestone Automation"`).
   - If that indicator is already on the job (you saw it in `get_job_indicators`):
     `update_job_indicator_notes(job_id=<action.job_id>, name="Bluestone Automation", notes=<action.marker_note_after>)`
   - If not yet on the job:
     `add_job_indicator(job_id=<action.job_id>, name="Bluestone Automation", notes=<action.marker_note_after>)`
4. Re-read `get_job_indicators(<action.job_id>)` and confirm the "Bluestone
   Automation" note now contains the new line. **If the marker write failed,
   say so loudly in your summary and in a push notification** — the next run may
   double-send.

Process actions one job at a time (send, then mark, then next).

If `dry_run` is true: send nothing, write no markers, just report what would
have happened.

## 5. Summary

```
rm -f jobs.json threads.json
```

Report: dry_run state; every message sent (to which number); every marker
written (and any that failed); any escalations with the customer's name; any
errors. **Do not commit** — the repo is read-only for the routine.
