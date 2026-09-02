# Runbook — how the automation actually runs

The `bluestone/` package is a **decision engine only**. It never talks to
RevDek, Quo, or Claude. A scheduled runner (a Claude Code scheduled task, or
any cron job that can reach the RevDek integration) does the I/O and calls the
engine in between.

Run cadence: every `poller.interval_minutes` (default 60).

---

## Each run

### 1. Refresh completed jobs

- `search_jobs(completed=true, limit=50)`
- For any job **not already in the DB**, also `get_job(job_id)` for full notes,
  and look up the customer's phone (`search_customers` / customer record).
- Build `jobs.json`: a list of raw job objects, each with an extra
  `"customer_full": { "first_name", "last_name", "phone" }`.

### 2. Plan + send check-ins

```
python -m bluestone.engine checkins --jobs jobs.json
```

Returns `actions`. For each `kind: "send_sms"` action:

- **dry_run true** (current): do nothing, just record the intent.
- **dry_run false**: `send_customer_message(to=<action.to>, text=<action.body>, provider="quo")` (not the deprecated send_sms)
  from the dedicated number.

Then tell the engine what was sent so state advances exactly once:

```
python -m bluestone.engine apply --results results.json
```

`results.json` = the list of actions you actually executed (same shape, add
`provider_message_id` if you have it).

### 3. Read replies + branch

- `list_recent_conversations(provider="quo")`, and for each,
  `get_conversation_messages(conversation_id)`.
- For every **inbound** message newer than our last check-in on that number:
  - Classify it. In a Claude scheduled task, classify directly using
    `classification.system_prompt` from `config.yml` → one word.
  - ```
    python -m bluestone.engine reply --jobs jobs.json --phone <customer #> \
        --text "<their message>" --classification <SATISFIED|DISSATISFIED|UNCLEAR>
    ```
  - Execute the returned actions:
    - `send_sms` (stage `closeout` or `clarify`) → send to the customer.
    - `notify_anderson` (stage `escalation`) → send to `escalation.sms_to`
      (or email if `method: email`).
  - `python -m bluestone.engine apply --results results.json`

If you omit `--classification`, the engine falls back to its built-in keyword
classifier (fine for dry-run, not recommended for production).

### 4. (optional) Status

```
python -m bluestone.engine status
```

---

## Going live checklist

1. `python -m bluestone.engine status` shows `unfilled_placeholders: []`.
2. A2P 10DLC registered in Quo (`a2p_10dlc_registered: true`).
3. Dedicated number visible under RevDek's Quo connection.
4. Run 24–48h with `dry_run: true`, review `message_log` and
   `classification_log` in the SQLite DB.
5. Send yourself a real end-to-end test (book a fake completed job, reply).
6. Flip `sending.dry_run: false`.

## Safety properties (enforced in code)

- Closeout is sent **only** on an explicit `SATISFIED` classification.
- One send per stage per job (idempotent) — re-running a poll never double-texts.
- `STOP` / opt-out → thread marked `opted_out`, number never messaged again.
- Every classification + the exact reply text is written to `classification_log`.
- Check-in only goes out inside the 08:30–19:00 CT window.
