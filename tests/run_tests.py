"""Zero-dependency test runner:  python3 tests/run_tests.py"""
from __future__ import annotations

import copy
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BLUESTONE_ESCALATION_SMS", "+12055550000")
os.environ.setdefault("BLUESTONE_ESCALATION_EMAIL", "alerts@example.com")
os.environ.setdefault("BLUESTONE_FROM_NUMBER", "+12055559999")

from bluestone.config import load_config, unfilled_placeholders
from bluestone import quotes, window_plans, templates, timing, classify, state, markers, pipeline
from bluestone.jobs import normalize_job, normalize_phone

CFG = load_config(ROOT / "config.yml")
CT = ZoneInfo("America/Chicago")
UTC = timezone.utc

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  ok   {name}")
    else:
        _f += 1
        print(f"  FAIL {name}  {detail}")


def msg(direction, text, at):
    return {"direction": direction, "text": text, "at": at}


def cfg_with(**over):
    c = copy.deepcopy(CFG)
    c["sending"]["halted"] = False   # test the logic, not the kill switch
    for k, v in over.items():
        section, _, key = k.partition(".")
        c[section][key] = v
    return c


# ---------------------------------------------------------------------------
print("config")
check("env var expanded", CFG["escalation"]["sms_to"] == "+12055550000")
check("no unfilled placeholders", unfilled_placeholders(CFG) == [], unfilled_placeholders(CFG))

print("quote parsing")
q = quotes.parse_future_quotes("did driveway. future quotes: roof wash $700, driveway pressure wash: $300", CFG)
check("real example", q == [{"service": "roof wash", "amount": "700"},
                            {"service": "driveway pressure wash", "amount": "300"}], q)
check("no section", quotes.parse_future_quotes("just notes", CFG) == [])
qd = quotes.parse_future_quotes("future quotes: $500 roof wash, $325 exterior window cleaning", CFG)
check("price-first", qd == [{"service": "roof wash", "amount": "500"},
                            {"service": "exterior window cleaning", "amount": "325"}], qd)
check("render", quotes.render_quote_list(q, CFG) == "- Roof Wash: $700\n- Driveway Pressure Wash: $300")

print("closing-quotes indicator")
ci = quotes.parse_closing_quotes("Windows -$300\nRoof-", CFG)
check("indicator real example, blank 'Roof-' skipped", ci == [{"service": "Windows", "amount": "300"}], ci)
ci2 = quotes.parse_closing_quotes("Windows-$570\nHouse Wash - $345\nGutters-", CFG)
check("indicator: no-price lines dropped, prices kept",
      ci2 == [{"service": "Windows", "amount": "570"}, {"service": "House Wash", "amount": "345"}], ci2)
check("indicator all blank -> []", quotes.parse_closing_quotes("Roof-\nWindows-", CFG) == [])
# parse_job_quotes: indicator wins over notes
jobq = {"notes": "future quotes: siding wash $999",
        "indicators": [{"name": "Closing Quotes Given", "notes": "Windows -$300"}]}
check("indicator beats notes", quotes.parse_job_quotes(jobq, CFG) == [{"service": "Windows", "amount": "300"}])
# empty/no-price indicator -> fall back to notes
jobq2 = {"notes": "future quotes: siding wash $999",
         "indicators": [{"name": "Closing Quotes Given", "notes": "Roof-"}]}
check("blank indicator falls back to notes",
      quotes.parse_job_quotes(jobq2, CFG) == [{"service": "siding wash", "amount": "999"}])
# no indicator -> notes
check("no indicator -> notes", quotes.parse_job_quotes({"notes": "future quotes: roof $400"}, CFG)
      == [{"service": "roof", "amount": "400"}])
check("normalize_job pulls indicator note",
      normalize_job({"id": "x", "date": "2026-09-02", "indicators": [
          {"name": "Closing Quotes Given", "notes": "Windows -$300"}]}, None)["closing_quotes_note"] == "Windows -$300")
# bare leading number, no $ sign, price-first with trailing unrelated narrative (real tech note)
bare = quotes.parse_closing_quotes("750 roof, she said she would wait", CFG)
check("bare leading number parses as a price", bare == [{"service": "roof", "amount": "750"}], bare)
check("small incidental numbers below threshold are not prices",
      quotes.parse_closing_quotes("2 window screens, 3 dogs on site", CFG) == [])

print("window plans")
check("no window svc", window_plans.render_window_block(["Pressure Washing"], 400, CFG) == "")
b = window_plans.render_window_block(["Pressure Washing", "Window Cleaning"], 380, CFG)
check("bundled = percentages", "15% off" in b and "$" not in b, b)
o = window_plans.render_window_block(["Window Cleaning"], 199, CFG)
check("window-only = priced", "$169" in o and "$159" in o, o)

print("templates")
job = normalize_job({"id": "j1", "service_type": ["House Wash"], "price": 350, "date": "2026-09-01",
                     "notes": "clean soffits", "customer": {"first_name": "anderson", "last_name": "oneal"}},
                    {"first_name": "anderson", "last_name": "oneal", "phone": "2055551234"})
ci = templates.render_check_in(job, CFG)
check("check-in name capitalized", ci.startswith("Hey Anderson "), ci)
co = templates.render_closeout(job, CFG)
check("no-quotes closeout: no quote lines, has referral+review",
      not co["has_quotes"] and "refer a friend" in co["body"] and "review" in co["body"])
check("no triple blank", "\n\n\n" not in co["body"])

print("templates - credit card invoice check-in")
job_cc = normalize_job({"id": "cc1", "service_type": ["Pressure Washing"], "price": 300, "date": "2026-09-17",
                        "notes": "", "customer": {"first_name": "summer", "last_name": "oneal"},
                        "indicators": [{"name": "Payment Collected",
                                        "notes": "Credit Card · $300.00\nhttps://revdek.ai/p/abc123"}]},
                       {"first_name": "summer", "last_name": "oneal", "phone": "+12054273210"})
check("needs_invoice extracted", job_cc["needs_invoice"] is True)
check("invoice_link extracted", job_cc["invoice_link"] == "https://revdek.ai/p/abc123")
ci_cc = templates.render_check_in(job_cc, CFG)
check("invoice check-in wording + link", ci_cc ==
      "Hey Summer thank you for your business! Here's the invoice whenever you're ready. "
      "How did everything turn out? https://revdek.ai/p/abc123", ci_cc)
job_cc_nolink = normalize_job({"id": "cc2", "service_type": ["Pressure Washing"], "price": 300, "date": "2026-09-17",
                               "notes": "", "customer": {"first_name": "bob", "last_name": "jones"},
                               "indicators": [{"name": "Payment Collected", "notes": "Credit Card · $300.00"}]},
                              {"first_name": "bob", "last_name": "jones", "phone": "+12055559999"})
check("needs_invoice true, no link yet -> invoice_link None",
      job_cc_nolink["needs_invoice"] is True and job_cc_nolink["invoice_link"] is None)
job_check = normalize_job({"id": "chk", "service_type": ["Pressure Washing"], "price": 300, "date": "2026-09-17",
                           "notes": "", "customer": {"first_name": "amy", "last_name": "ray"},
                           "indicators": [{"name": "Payment Collected", "notes": "Check · $300.00"}]},
                          {"first_name": "amy", "last_name": "ray", "phone": "+12055550000"})
check("check payment -> needs_invoice False", job_check["needs_invoice"] is False)
check("normal check-in unaffected", templates.render_check_in(job_check, CFG).startswith("Hey Amy this is Anderson"))

print("timing")
due = timing.checkin_due_time({"date": "2026-09-01", "end_time": "14:00:00"}, CFG)
check("next_morning: due 9am the day after the job", due == datetime(2026, 9, 2, 9, 0, tzinfo=CT), due)
due2 = timing.checkin_due_time({"date": "2026-09-01", "end_time": "23:30:00"}, CFG)
check("next_morning: long job still just 9am next day", due2 == datetime(2026, 9, 2, 9, 0, tzinfo=CT), due2)
check("no date -> None", timing.checkin_due_time({}, CFG) is None)
CFG_HAE = cfg_with(**{"initial_followup.schedule": "hours_after_end"})
h1 = timing.checkin_due_time({"date": "2026-09-01", "end_time": "14:00:00"}, CFG_HAE)
check("hours_after_end: 14:00 -> 19:00 same day", h1.day == 1 and h1.hour == 19, h1)
h2 = timing.checkin_due_time({"date": "2026-09-01", "end_time": "15:00:00"}, CFG_HAE)
check("hours_after_end: 15:00 -> next day 08:30", h2.day == 2 and (h2.hour, h2.minute) == (8, 30), h2)

print("classify")
check("positive", classify.classify_rulebased("Looks great, thank you!")[0] == "SATISFIED")
check("negative", classify.classify_rulebased("still streaks on the windows")[0] == "DISSATISFIED")
check("mixed -> unclear", classify.classify_rulebased("looks good but you missed a spot")[0] == "UNCLEAR")
check("call me -> contact", classify.classify_rulebased("Call me")[0] == "CONTACT_REQUEST")
check("contact beats positive", classify.classify_rulebased("looks good, text me back about the gate")[0] == "CONTACT_REQUEST")
check("normalize CONTACT_REQUEST", classify.normalize("CONTACT_REQUEST")[0] == "CONTACT_REQUEST")
check("normalize DISSATISFIED not SATISFIED", classify.normalize("DISSATISFIED")[0] == "DISSATISFIED")

print("phone")
check("10 digit", normalize_phone("205-555-0142") == "+12055550142")
check("11 digit", normalize_phone("1 205 555 0142") == "+12055550142")

# ---------------------------------------------------------------------------
print("state.derive")
CHECKIN = templates.render_check_in(job, CFG)
CLOSEOUT = templates.render_closeout(job, CFG)["body"]
t0 = datetime(2026, 9, 2, 12, 0, tzinfo=CT)

check("empty thread -> no_thread",
      state.derive(job, [], t0, CFG)["stage"] == "no_thread")
check("checkin only -> awaiting_reply",
      state.derive(job, [msg("outbound", CHECKIN, t0)], t0, CFG)["stage"] == "awaiting_reply")
th_sat = [msg("outbound", CHECKIN, t0), msg("inbound", "looks great thanks", t0 + timedelta(minutes=5))]
s_sat = state.derive(job, th_sat, t0 + timedelta(minutes=6), CFG)
check("checkin + positive reply -> closeout_pending", s_sat["stage"] == "closeout_pending")
th_done = th_sat + [msg("outbound", CLOSEOUT, t0 + timedelta(minutes=10))]
check("closeout in thread -> closed_satisfied",
      state.derive(job, th_done, t0 + timedelta(hours=1), CFG)["stage"] == "closed_satisfied")
th_coreply = th_done + [msg("inbound", "actually I'd love the quarterly plan", t0 + timedelta(minutes=30))]
sc = state.derive(job, th_coreply, t0 + timedelta(minutes=31), CFG)
check("reply after closeout -> closeout_reply", sc["stage"] == "closeout_reply")
check("closeout_reply captures the reply", sc["last_reply"]["text"] == "actually I'd love the quarterly plan")
check("STOP after closeout -> opted_out (not closeout_reply)",
      state.derive(job, th_done + [msg("inbound", "STOP", t0 + timedelta(minutes=30))],
                   t0 + timedelta(minutes=31), CFG)["stage"] == "opted_out")
th_stop = [msg("outbound", CHECKIN, t0), msg("inbound", "STOP", t0 + timedelta(minutes=1))]
check("STOP -> opted_out", state.derive(job, th_stop, t0, CFG)["stage"] == "opted_out")
th_neg = [msg("outbound", CHECKIN, t0), msg("inbound", "there are still streaks everywhere", t0 + timedelta(minutes=5))]
check("negative reply -> needs_escalation",
      state.derive(job, th_neg, t0 + timedelta(minutes=6), CFG)["stage"] == "needs_escalation")
th_unclear = [msg("outbound", CHECKIN, t0), msg("inbound", "hmm", t0 + timedelta(minutes=5))]
check("unclear -> needs_escalation directly (no clarifying text)",
      state.derive(job, th_unclear, t0 + timedelta(minutes=6), CFG)["stage"] == "needs_escalation")
th_mixed = [msg("outbound", CHECKIN, t0),
           msg("inbound", "everything was good except some grease stains on the driveway",
               t0 + timedelta(minutes=5))]
check("mixed positive+negative reply -> needs_escalation, not closeout_pending",
      state.derive(job, th_mixed, t0 + timedelta(minutes=6), CFG)["stage"] == "needs_escalation")
check("checkin detection tolerates different first name",
      state.is_checkin("Hey Bob this is Anderson. Thank you for your business we really appreciate it! How did everything turn out?", job, CFG))
# a prior completed follow-up cycle for the same number must NOT suppress a new job
job_new = normalize_job({"id": "jn", "service_type": ["House Wash"], "price": 300,
                         "date": "2026-09-05", "end_time": "16:00:00", "notes": "x",
                         "customer": {"first_name": "Amy", "last_name": "Ray"}},
                        {"first_name": "Amy", "last_name": "Ray", "phone": "+12055551234"})
old_cycle = [msg("outbound", CHECKIN, datetime(2026, 9, 1, 9, 0, tzinfo=CT)),
             msg("inbound", "looks great thanks", datetime(2026, 9, 1, 9, 5, tzinfo=CT)),
             msg("outbound", CLOSEOUT, datetime(2026, 9, 1, 9, 8, tzinfo=CT))]
check("old cycle before this job's date is ignored -> no_thread",
      state.derive(job_new, old_cycle, datetime(2026, 9, 6, 10, 0, tzinfo=CT), CFG)["stage"] == "no_thread")

print("state.derive - 'Skip to Closing Text' manual override")
job_fs = normalize_job({"id": "fs1", "service_type": ["House Wash"], "price": 350, "date": "2026-09-01",
                        "end_time": "12:00:00", "notes": "x",
                        "customer": {"first_name": "Amy", "last_name": "Ray"},
                        "indicators": [{"name": "Skip to Closing Text", "notes": None}]},
                       {"first_name": "Amy", "last_name": "Ray", "phone": "+12055551234"})
check("force_satisfied flag extracted from indicator", job_fs["force_satisfied"] is True)
check("no thread at all -> still fast-tracked to closeout_pending",
      state.derive(job_fs, [], t0, CFG)["stage"] == "closeout_pending")
check("checkin sent, no reply yet -> fast-tracked to closeout_pending",
      state.derive(job_fs, [msg("outbound", CHECKIN, t0)], t0, CFG)["stage"] == "closeout_pending")
th_fs_neg = [msg("outbound", CHECKIN, t0), msg("inbound", "still streaks everywhere", t0 + timedelta(minutes=5))]
check("would-have-escalated reply -> overridden to closeout_pending",
      state.derive(job_fs, th_fs_neg, t0 + timedelta(minutes=6), CFG)["stage"] == "closeout_pending")
check("STOP still wins over the override -> opted_out",
      state.derive(job_fs, [msg("inbound", "STOP", t0)], t0, CFG)["stage"] == "opted_out")
check("satisfied_at is the next-morning slot, not immediate",
      state.derive(job_fs, [], t0, CFG)["satisfied_at"] == timing.checkin_due_time(job_fs, CFG))
job_no_fs = normalize_job({"id": "nofs", "service_type": ["House Wash"], "price": 350, "date": "2026-09-01",
                           "notes": "x", "customer": {"first_name": "Amy", "last_name": "Ray"}}, None)
check("job without the indicator -> flag is False", job_no_fs["force_satisfied"] is False)

# closeout via the override has a different opener, and the thread-based
# stage machine must still recognize it (no check-in ever precedes it) so a
# reply after it correctly triggers closeout_reply, not an infinite resend.
FS_CLOSEOUT = templates.render_closeout(job_fs, CFG)["body"]
check("override opener is the 'thanks again' line, not 'Glad to hear it!'",
      FS_CLOSEOUT.startswith("Hey Amy thanks again for your business!"), FS_CLOSEOUT[:60])
th_fs_sent = [msg("outbound", FS_CLOSEOUT, t0 + timedelta(minutes=2))]
check("override closeout recognized with no check-in in thread -> closed_satisfied",
      state.derive(job_fs, th_fs_sent, t0 + timedelta(hours=1), CFG)["stage"] == "closed_satisfied")
th_fs_reply = th_fs_sent + [msg("inbound", "thanks, i'll keep the quote in mind", t0 + timedelta(minutes=5))]
check("reply after override closeout -> closeout_reply (not re-triggered as closeout_pending)",
      state.derive(job_fs, th_fs_reply, t0 + timedelta(hours=1), CFG)["stage"] == "closeout_reply")

print("state.already_escalated")
esc_body = templates.render_escalation(job, "bad", CFG)  # contains customer phone +12055551234
ath = [msg("outbound", esc_body, t0)]
check("alert with phone -> already escalated",
      state.already_escalated(job, ath, t0 + timedelta(hours=1), CFG))
check("old alert -> not deduped",
      not state.already_escalated(job, ath, t0 + timedelta(days=40), CFG))
check("no anderson thread -> not escalated",
      not state.already_escalated(job, [], t0, CFG))

# ---------------------------------------------------------------------------
print("pipeline.plan (stateless, end to end)")
CFGP = cfg_with(**{"sending.job_allowlist": [], "sending.completed_since": "2026-09-01",
                   "sending.max_job_age_days": 30})
jobs = [
    normalize_job({"id": "A", "service_type": ["House Wash"], "price": 350, "date": "2026-09-01",
                   "end_time": "12:00:00", "completed": True, "notes": "future quotes: roof wash $600",
                   "customer": {"first_name": "Amy", "last_name": "Ray"}},
                  {"first_name": "Amy", "last_name": "Ray", "phone": "+12055550101"}),
    normalize_job({"id": "B", "service_type": ["Window Cleaning"], "price": 200, "date": "2026-09-01",
                   "end_time": "12:00:00", "completed": True, "notes": "all glass",
                   "customer": {"first_name": "Bob", "last_name": "Kim"}},
                  {"first_name": "Bob", "last_name": "Kim", "phone": "+12055550102"}),
]
now = datetime(2026, 9, 2, 10, 0, tzinfo=CT)   # morning after the job, past 9am
esc_num = CFG["escalation"]["sms_to"]

# 0. hard kill switch -> zero send/notify actions
CFG_HALT = cfg_with(**{"sending.job_allowlist": [], "sending.completed_since": "2026-09-01"})
CFG_HALT["sending"]["halted"] = True
acts = pipeline.plan(jobs, {}, now, CFG_HALT)
check("halted -> no send/notify actions",
      not [a for a in acts if a.kind in ("send_sms", "notify_anderson")], [a.as_dict() for a in acts])

# --- idempotency markers ---------------------------------------
print("markers")
n0 = markers.add(None, "checkin", datetime(2026, 9, 10, 14, 3, tzinfo=timezone.utc))
check("add checkin to empty note", n0 == "checkin sent 2026-09-10T14:03:00Z", n0)
n1 = markers.add(n0, "closeout", datetime(2026, 9, 10, 15, 20, tzinfo=timezone.utc))
check("add closeout appends line", n1.splitlines()[-1] == "closeout sent 2026-09-10T15:20:00Z", n1)
check("add is idempotent", markers.add(n1, "checkin", datetime(2026, 9, 11, tzinfo=timezone.utc)) == n1)
check("parse", markers.parse(n1) == {"checkin", "closeout"})
check("has", markers.has(n1, "closeout") and not markers.has(n1, "escalated"))
check("unrecognized 'clarify' token in an old note is ignored, not an error",
      markers.parse("clarify sent 2026-09-01T00:00:00Z") == set())
check("escalated line has no 'sent'", markers.add(None, "escalated", datetime(2026, 9, 10, tzinfo=timezone.utc)) == "escalated 2026-09-10T00:00:00Z")
jm = {"indicators": [{"name": "Bluestone Automation", "notes": "checkin sent 2026-09-10T14:00:00Z"}]}
check("job_marker_note by indicator name", markers.job_marker_note(jm, CFG) == "checkin sent 2026-09-10T14:00:00Z")
check("job_marker_note none when absent", markers.job_marker_note({"indicators": []}, CFG) is None)
jm_dup = {"indicators": [{"name": "Bluestone Automation", "notes": "checkin sent 2026-09-10T14:00:00Z"},
                         {"name": "Bluestone Automation", "notes": "closeout sent 2026-09-10T16:00:00Z"}]}
check("job_marker_note merges duplicate indicator entries",
      markers.parse(markers.job_marker_note(jm_dup, CFG)) == {"checkin", "closeout"},
      markers.job_marker_note(jm_dup, CFG))

# marker suppresses a resend even when the thread is stale
jobs_m = [normalize_job({"id": "M", "service_type": ["House Wash"], "price": 300, "date": "2026-09-01",
                         "end_time": "12:00:00", "completed": True, "notes": "future quotes: roof $500",
                         "indicators": [{"name": "Bluestone Automation", "notes": "checkin sent 2026-09-02T08:00:00Z"}],
                         "customer": {"first_name": "Mia", "last_name": "K"}},
                        {"first_name": "Mia", "last_name": "K", "phone": "+12055550301"})]
acts = pipeline.plan(jobs_m, {}, now, CFGP)   # empty threads = stale = would normally re-send checkin
check("checkin marker blocks resend on stale thread",
      not [a for a in acts if a.kind == "send_sms"], [a.as_dict() for a in acts])
# a real checkin action carries the marker instructions
acts = pipeline.plan([normalize_job({"id": "N", "service_type": ["House Wash"], "price": 300, "date": "2026-09-01",
                                     "end_time": "12:00:00", "completed": True, "notes": "x",
                                     "customer": {"first_name": "Ned", "last_name": "P"}},
                                    {"first_name": "Ned", "last_name": "P", "phone": "+12055550302"})], {}, now, CFGP)
ck = [a for a in acts if a.stage == "checkin" and a.kind == "send_sms"][0]
check("checkin action carries marker + note + indicator",
      ck.marker == "checkin" and "checkin sent" in ck.marker_note_after
      and ck.marker_indicator == "Bluestone Automation", ck.as_dict())

# unresolved ${VAR} escalation contact -> refuse to do anything this run
CFG_UNRESOLVED = cfg_with(**{"sending.job_allowlist": [], "sending.completed_since": "2026-09-01"})
CFG_UNRESOLVED["escalation"]["sms_to"] = "${BLUESTONE_ESCALATION_SMS}"
acts = pipeline.plan(jobs, {}, now, CFG_UNRESOLVED)
check("unresolved escalation contact -> single config_error note, nothing else",
      len(acts) == 1 and acts[0].stage == "config_error", [a.as_dict() for a in acts])

# duplicate job entries in one call -> only one action, never two sends
dup_job = normalize_job({"id": "DUP", "service_type": ["House Wash"], "price": 300, "date": "2026-09-01",
                         "end_time": "12:00:00", "completed": True, "notes": "x",
                         "customer": {"first_name": "Doug", "last_name": "R"}},
                        {"first_name": "Doug", "last_name": "R", "phone": "+12055550303"})
acts = pipeline.plan([dup_job, dup_job, dup_job], {}, now, CFGP)
check("duplicate job entries collapse to one action",
      len([a for a in acts if a.kind == "send_sms" and a.job_id == "DUP"]) == 1,
      [a.as_dict() for a in acts])

# 1. no threads -> two check-ins
acts = pipeline.plan(jobs, {}, now, CFGP)
check("both check-ins planned", len([a for a in acts if a.stage == "checkin"]) == 2, [a.as_dict() for a in acts])

# 2. check-in already in thread -> not resent
ciA = templates.render_check_in(jobs[0], CFGP)
threads = {"+12055550101": [msg("outbound", ciA, now - timedelta(hours=1))]}
acts = pipeline.plan(jobs, threads, now, CFGP)
check("A not resent, B still planned",
      [a.stage for a in acts if a.kind == "send_sms"] == ["checkin"]
      and acts and all(a.job_id != "A" or a.kind != "send_sms" for a in acts),
      [a.as_dict() for a in acts])

# 3. A replied happy 5 min ago -> closeout pending (not yet due: 2 min delay already passed)
threads["+12055550101"].append(msg("inbound", "turned out great thanks", now - timedelta(minutes=5)))
acts = pipeline.plan(jobs, threads, now, CFGP)
a_close = [a for a in acts if a.job_id == "A" and a.stage == "closeout"]
check("A closeout sent after delay", a_close and a_close[0].kind == "send_sms", [a.as_dict() for a in acts])
check("A closeout has quote", "Roof Wash: $600" in a_close[0].body)

# 4. A replied happy 30s ago -> closeout waits
threads2 = {"+12055550101": [msg("outbound", ciA, now - timedelta(hours=1)),
                             msg("inbound", "great", now - timedelta(seconds=30))]}
acts = pipeline.plan(jobs, threads2, now, CFGP)
a_close = [a for a in acts if a.job_id == "A" and a.stage == "closeout"]
check("A closeout held < 2 min", a_close and a_close[0].kind == "note", [a.as_dict() for a in acts])

# 5. B replied unhappy -> escalation to esc number
ciB = templates.render_check_in(jobs[1], CFGP)
threads3 = {"+12055550102": [msg("outbound", ciB, now - timedelta(hours=1)),
                             msg("inbound", "you left a mess on the porch", now - timedelta(minutes=2))]}
acts = pipeline.plan(jobs, threads3, now, CFGP)
esc = [a for a in acts if a.job_id == "B" and a.stage == "escalation"]
check("B escalates", esc and esc[0].kind == "notify_anderson" and esc[0].to == esc_num, [a.as_dict() for a in acts])

# 6. B already escalated (alert in esc thread) -> not re-escalated
threads3[esc_num] = [msg("outbound", esc[0].body, now - timedelta(minutes=1))]
acts = pipeline.plan(jobs, threads3, now, CFGP)
check("B not re-escalated", not [a for a in acts if a.job_id == "B" and a.kind == "notify_anderson"],
      [a.as_dict() for a in acts])

# 7. contact request -> escalation even though positive
threads4 = {"+12055550102": [msg("outbound", ciB, now - timedelta(hours=1)),
                             msg("inbound", "looks good, but call me about a gutter quote", now - timedelta(minutes=2))]}
acts = pipeline.plan(jobs, threads4, now, CFGP)
cr = [a for a in acts if a.job_id == "B"]
check("contact request -> notify_anderson", cr and cr[0].kind == "notify_anderson", [a.as_dict() for a in acts])
check("contact request body mentions call", "call" in cr[0].body.lower())

# 7b. reply AFTER the closeout -> notify Anderson, nothing to customer
coA = templates.render_closeout(jobs[0], CFGP)["body"]
th_cr = {"+12055550101": [
    msg("outbound", ciA, now - timedelta(hours=3)),
    msg("inbound", "looks great", now - timedelta(hours=2, minutes=50)),
    msg("outbound", coA, now - timedelta(hours=2, minutes=45)),
    msg("inbound", "actually can I get on the quarterly plan?", now - timedelta(minutes=10)),
]}
acts = pipeline.plan(jobs, th_cr, now, CFGP)
crx = [a for a in acts if a.job_id == "A"]
check("post-closeout reply -> notify_anderson only",
      len(crx) == 1 and crx[0].kind == "notify_anderson" and crx[0].stage == "closeout_reply"
      and crx[0].to == esc_num, [a.as_dict() for a in acts])
check("post-closeout alert quotes the customer message",
      "quarterly plan" in crx[0].body, crx[0].body)
# already alerted -> no repeat
th_cr[esc_num] = [msg("outbound", crx[0].body, now - timedelta(minutes=5))]
acts = pipeline.plan(jobs, th_cr, now, CFGP)
check("post-closeout reply not re-alerted",
      not [a for a in acts if a.job_id == "A" and a.kind == "notify_anderson"], [a.as_dict() for a in acts])
# feature off -> ignored
CFG_OFF = cfg_with(**{"sending.job_allowlist": [], "sending.completed_since": "2026-09-01",
                      "sending.max_job_age_days": 30})
CFG_OFF["escalation"]["notify_on_closeout_reply"] = False
del th_cr[esc_num]
acts = pipeline.plan(jobs, th_cr, now, CFG_OFF)
check("notify_on_closeout_reply=false -> nothing", not [a for a in acts if a.job_id == "A"],
      [a.as_dict() for a in acts])

# 8. scope: job outside allowlist ignored
CFG_AL = cfg_with(**{"sending.job_allowlist": ["A"], "sending.completed_since": "2026-09-01",
                     "sending.max_job_age_days": 30})
acts = pipeline.plan(jobs, {}, now, CFG_AL)
check("allowlist filters to A only", {a.job_id for a in acts if a.kind == "send_sms"} == {"A"},
      [a.as_dict() for a in acts])

# 9. scope: too-old job ignored
old = normalize_job({"id": "OLD", "service_type": ["House Wash"], "price": 300, "date": "2026-07-01",
                     "end_time": "12:00:00", "completed": True, "notes": "x",
                     "customer": {"first_name": "Old", "last_name": "Job"}},
                    {"first_name": "Old", "last_name": "Job", "phone": "+12055550199"})
acts = pipeline.plan([old], {}, now, CFGP)
check("stale job (max_job_age_days) ignored", not [a for a in acts if a.kind == "send_sms"], [a.as_dict() for a in acts])

# 10. not-yet-due check-in (before 9am the morning after)
early = datetime(2026, 9, 2, 8, 0, tzinfo=CT)
acts = pipeline.plan(jobs, {}, early, CFGP)
check("check-in not due before 9am -> nothing", not [a for a in acts if a.kind == "send_sms"], [a.as_dict() for a in acts])
# same-day as job -> not due
acts = pipeline.plan(jobs, {}, datetime(2026, 9, 1, 20, 0, tzinfo=CT), CFGP)
check("check-in not due same day as job", not [a for a in acts if a.kind == "send_sms"], [a.as_dict() for a in acts])

# 11. credit-card + invoice link: sends immediately, same day, ignoring the
# next-morning window entirely (the one exception to that rule).
cc_job = normalize_job({"id": "CC", "service_type": ["Pressure Washing"], "price": 300, "date": "2026-09-01",
                        "end_time": "12:00:00", "completed": True, "notes": "",
                        "customer": {"first_name": "Cara", "last_name": "Cole"},
                        "indicators": [{"name": "Payment Collected",
                                        "notes": "Credit Card · $300.00\nhttps://revdek.ai/p/xyz789"}]},
                       {"first_name": "Cara", "last_name": "Cole", "phone": "+12055550301"})
same_day_afternoon = datetime(2026, 9, 1, 15, 0, tzinfo=CT)   # same day as the job, well before 9am next day
acts = pipeline.plan([cc_job], {}, same_day_afternoon, CFGP)
sends = [a for a in acts if a.kind == "send_sms"]
check("invoice check-in sent same-day, not gated to next morning", len(sends) == 1, [a.as_dict() for a in acts])
check("invoice check-in has the link", sends and "https://revdek.ai/p/xyz789" in sends[0].body,
      sends[0].body if sends else None)

# 12. credit-card marked but link not pasted yet -> waits, sends nothing
cc_nolink = normalize_job({"id": "CCNL", "service_type": ["Pressure Washing"], "price": 300, "date": "2026-09-01",
                           "end_time": "12:00:00", "completed": True, "notes": "",
                           "customer": {"first_name": "Dana", "last_name": "Diaz"},
                           "indicators": [{"name": "Payment Collected", "notes": "Credit Card · $300.00"}]},
                          {"first_name": "Dana", "last_name": "Diaz", "phone": "+12055550302"})
acts = pipeline.plan([cc_nolink], {}, same_day_afternoon, CFGP)
check("no link yet -> no send_sms at all", not [a for a in acts if a.kind == "send_sms"], [a.as_dict() for a in acts])

print()
print(f"{_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
