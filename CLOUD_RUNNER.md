# Cloud runner playbook

What the scheduled cloud routine does every hour. The repo is your working
directory. You have the **RevDek connector**. There is **no database** — you
figure out what to do by reading the conversation threads from RevDek.

Tool names (server prefix varies — match by name): `search_jobs`, `get_job`,
`search_customers`, `search_customer_conversations`, `get_customer_timeline`,
`get_conversation_messages`, `list_recent_conversations`, `send_customer_message`.

**Every outbound text: `send_customer_message(to=..., text=..., provider="quo")`.**
Never `send_sms`.

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

`status` echoes `dry_run` and `job_allowlist`. **If `dry_run` is true (current):
send NOTHING** — run `plan`, then just write the summary. If a job comes back
needing escalation, name the customer in your summary but text no one.

## 1. Gather jobs

1. `search_jobs(completed=true, limit=50)`.
2. If `status` showed a non-empty `job_allowlist`, drop every job not on it.
3. Also drop jobs whose `date` is more than ~2 weeks old.
4. For each remaining job, `get_job(job_id)` for the full `notes`, and find the
   customer's phone (`search_customers` on the name / `customer_id`).
5. Write `jobs.json`: a JSON list of the raw job objects, each with
   `"customer_full": {"first_name","last_name","phone"}`.

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
| `send_sms` | `checkin` | send `body` to `to` (the customer) |
| `send_sms` | `closeout` | send `body` to the customer |
| `send_sms` | `clarify` | send `body` to the customer |
| `note` | `closeout` | closeout not due yet (2-min delay) — do nothing |
| `notify_anderson` | `escalation` | send `body` to `to` (the escalation number) — **not the customer** |
| `note` | anything | informational — do nothing |

**If `dry_run` is false**, execute each `send_sms` / `notify_anderson` with
`send_customer_message`. If `dry_run` is true, execute nothing.

Idempotency is automatic: the engine reads the thread, so if our check-in /
closeout / clarify is already there it won't be in the actions. An escalation
that was already texted to the escalation number won't repeat.

## 4. Summary

```
rm -f jobs.json threads.json
```

Report: dry_run state, how many check-ins / closeouts / clarifies were planned
(and sent, if live), any escalations with the customer's name, and any errors.

**Do not commit anything** — this repo is read-only for the routine; there's no
state to save.
