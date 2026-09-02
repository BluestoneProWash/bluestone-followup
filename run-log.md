# Run log — bluestone-followup-runner

## 2026-09-02 10:06 CDT — manual run (dry_run: true)

**Scope:** job_allowlist active — Anderson O'Neal (f7297c88…), Dad Oneal (bbb9dc60…).

**Completed jobs:** both confirmed `completed: true` in RevDek. Both newly
tracked this run.

| job | customer | phone | seen completed | check-in scheduled |
|---|---|---|---|---|
| f7297c88… | Anderson O'Neal | +12056441282 | 10:06 CDT | **15:06 CDT today** |
| bbb9dc60… | Dad Oneal | +12053701827 | 10:06 CDT | **15:06 CDT today** |

**Check-ins sent/planned:** 0 sent (dry_run), 0 due yet — both scheduled for
15:06 CDT (completion + 5h, inside the 08:30–19:00 window).

**Replies processed:** 0. Quo conversations for both numbers contain only
outbound RevDek automation messages (booking / reschedule / job-complete
notices). No inbound customer replies.

**Notes:**
- One earlier outbound Quo message ("testing", 2026-09-02 04:48 CDT) shows
  status `failed` — predates today's setup; watch whether real sends deliver.
- Conversation display names are mismatched in Quo ("Dave Morey",
  "State Farm office") but the runner matches by phone number, so it's harmless.

**Errors:** none.

**Next:** the ~15:06 (and later) scheduled runs will send the two check-ins
once due — still dry_run, so they'll be logged here, not texted.

## 2026-09-02 ~10:10–10:27 CDT — manual end-to-end test (Anderson O'Neal)

- 10:10 & 10:14  check-in sent to +12056441282 (2nd send = reworded copy). Delivered.
- 10:15  customer reply "Great. Thank you." — appeared in RevDek ~2–3 min later
  (propagation lag, not an hour; earlier "1hr" read was a misread of lastSyncAt).
- 10:20  reply classified SATISFIED → closeout scheduled for 10:22:33 (+2 min).
- 10:27  closeout sent to +12056441282 (msg ACab1f9837978e4d6d833de496818f3ecd).
  Full text: quotes (Driveway Pressure Washing $325, Roof Wash $480) + priced
  window plan ($212 / $200 per visit) + referral + review link.
- Anderson job status → closed_satisfied.

**Issue found:** the scheduled tasks (hourly runner fired 10:21, one-time closeout
task fired 10:23) did NOT complete — `lastRunAt` was set but no DB / log changes.
Almost certainly paused waiting for tool-permission approval. Action: user must
click "Run now" on bluestone-followup-runner once and approve every tool prompt
(search_jobs, get_job, search_customers, get_conversation_messages,
send_customer_message, Bash/sqlite) so future runs don't stall.
The one-time closeout task was deleted to prevent a duplicate send.

## 2026-09-02 10:48 CDT — test run, new Anderson job (555c5e9b)

- New job: Anderson O'Neal, Sep 1, **Roof Soft Wash + Window Cleaning**, $945.
  notes: "future quotes: driveway pressure washing $275, house wash $325".
  Tests: window-cleaning-bundled-with-another-service closeout (percentages, no $).
- Old Anderson job f7297c88 was deleted in RevDek; its SQLite row (closed_satisfied) left as history.
- job_allowlist updated: 555c5e9b + bbb9dc60 (Dad).
- 10:48  check-in sent to +12056441282 (test shortcut — real send_time was 15:48). Job → awaiting_reply.
- Waiting on customer reply.

  - 10:49  reply "Turned out good, thank you" -> SATISFIED
  - 10:55  closeout sent to +12056441282 (msg AC87db01287e5141378fb12be4b1cd94b3):
    quotes (Driveway Pressure Washing $275, House Wash $325) + BUNDLED window block
    ("15% off semi-annual, 20% off quarterly" - percentages, no $) + referral + review.
  - job 555c5e9b -> closed_satisfied. PASS: window-cleaning-bundled closeout verified.

## 2026-09-02 ~11:00 CDT — config change

- `closeout.require_future_quotes` -> false. Closeout now always sends; with no
  "future quotes" it just drops that paragraph (window plan + referral + review
  still go). Reworded no-quotes referral line ("If you refer a friend...").
- Name title-casing added (customer records with lowercase names now greet
  "Hey Anderson" not "Hey anderson").
- 69 tests pass.

## 2026-09-02 11:08 CDT — test run, Anderson job 8b806412 (House Wash, NO future quotes)

- New job: Anderson O'Neal, Sep 1, House Wash $350, notes "clean all soffits, walls"
  — NO "future quotes" section, no window cleaning.
  Tests: closeout with require_future_quotes=false → sends referral + review only.
- allowlist: 8b806412 + bbb9dc60. Prior test job 555c5e9b deleted in RevDek.
- 11:08  check-in sent to +12056441282 (test shortcut). Job → awaiting_reply.
- Expected closeout on positive reply: "Glad to hear it!" + referral + review, no quotes, no window block.

  - 11:11  reply "Turned out great. I know it was a long and HOT day for Parker,
    but he did a great job." -> SATISFIED
  - 11:16  closeout sent to +12056441282 (msg AC861e85202de9421d89b1ab732ef3ea4f):
    "Glad to hear it!" + referral + review. NO quotes paragraph, NO window block.
  - job 8b806412 -> closed_satisfied. PASS: no-quotes / no-window closeout verified.

## 2026-09-02 ~11:25 CDT — new bucket: CONTACT_REQUEST

- "call me" / "text me back" / "have someone reach out" etc. -> escalates to
  Anderson immediately, NO clarifying reply, even if the tone is positive.
  Deterministic pre-check in pipeline + new classifier bucket + system-prompt rule.
  New template `templates.contact_request`. Config `classification.contact_request_phrases`.
- 81 tests pass.
