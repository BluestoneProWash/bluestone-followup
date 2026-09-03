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
from bluestone import quotes, window_plans, templates, timing, classify, state, pipeline
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
CLARIFY = templates.render_unclear(CFG)
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
th_stop = [msg("outbound", CHECKIN, t0), msg("inbound", "STOP", t0 + timedelta(minutes=1))]
check("STOP -> opted_out", state.derive(job, th_stop, t0, CFG)["stage"] == "opted_out")
th_neg = [msg("outbound", CHECKIN, t0), msg("inbound", "there are still streaks everywhere", t0 + timedelta(minutes=5))]
check("negative reply -> needs_escalation",
      state.derive(job, th_neg, t0 + timedelta(minutes=6), CFG)["stage"] == "needs_escalation")
th_unclear = [msg("outbound", CHECKIN, t0), msg("inbound", "hmm", t0 + timedelta(minutes=5))]
check("unclear, no clarify yet -> send_clarify",
      state.derive(job, th_unclear, t0 + timedelta(minutes=6), CFG)["stage"] == "send_clarify")
th_unclear2 = th_unclear + [msg("outbound", CLARIFY, t0 + timedelta(minutes=7))]
check("unclear, clarify already sent -> awaiting_reply",
      state.derive(job, th_unclear2, t0 + timedelta(minutes=8), CFG)["stage"] == "awaiting_reply")
th_unclear3 = th_unclear2 + [msg("inbound", "still not sure what you mean", t0 + timedelta(minutes=20))]
check("2nd unclear after clarify -> needs_escalation",
      state.derive(job, th_unclear3, t0 + timedelta(minutes=21), CFG)["stage"] == "needs_escalation")
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

print()
print(f"{_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
