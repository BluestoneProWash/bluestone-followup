"""Zero-dependency test runner:  python tests/run_tests.py"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bluestone.config import load_config
from bluestone import quotes, window_plans, templates, timing, classify, pipeline
from bluestone.jobs import normalize_job, normalize_phone
from bluestone.store import Store

CFG = load_config(ROOT / "config.yml")
CT = ZoneInfo("America/Chicago")

_passed = 0
_failed = 0


def check(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok   {name}")
    else:
        _failed += 1
        print(f"  FAIL {name}  {detail}")


# --- quote parsing --------------------------------------------------------
print("quote parsing")
q = quotes.parse_future_quotes(
    "Did the driveway. future quotes: roof wash $700, driveway pressure wash: $300", CFG
)
check("real example count", len(q) == 2, q)
check("real example values",
      q == [{"service": "roof wash", "amount": "700"},
            {"service": "driveway pressure wash", "amount": "300"}], q)
check("no section -> []", quotes.parse_future_quotes("just some notes", CFG) == [])
check("none notes -> []", quotes.parse_future_quotes(None, CFG) == [])
q2 = quotes.parse_future_quotes("Future Quotes - roof wash - $1,200; gutter clean $150.", CFG)
check("variation dashes/semicolon/comma-thousands",
      q2 == [{"service": "roof wash", "amount": "1200"},
             {"service": "gutter clean", "amount": "150"}], q2)
q3 = quotes.parse_future_quotes("future quote: soft wash $500\n\nunrelated note $99", CFG)
check("stops at blank line", q3 == [{"service": "soft wash", "amount": "500"}], q3)
# real test-job notes
qa = quotes.parse_future_quotes("future quotes: driveway pressure washing $325, roof wash $480", CFG)
check("Anderson test job notes",
      qa == [{"service": "driveway pressure washing", "amount": "325"},
             {"service": "roof wash", "amount": "480"}], qa)
qd = quotes.parse_future_quotes("future quotes: $500 roof wash, $325 exterior window cleaning", CFG)
check("Dad test job notes (price-first)",
      qd == [{"service": "roof wash", "amount": "500"},
             {"service": "exterior window cleaning", "amount": "325"}], qd)
check("render list",
      quotes.render_quote_list(q, CFG) == "- Roof Wash: $700\n- Driveway Pressure Wash: $300",
      quotes.render_quote_list(q, CFG))

# --- window plans -------------------------------------------------------
print("window plans")
check("no window svc -> ''", window_plans.render_window_block(["Pressure Washing"], 400, CFG) == "")
bundled = window_plans.render_window_block(["Pressure Washing", "Window Cleaning"], 380, CFG)
check("bundled -> percentages, no $", "15%" in bundled and "20%" in bundled and "$" not in bundled, bundled)
only = window_plans.render_window_block(["Window Cleaning"], 199, CFG)
check("window-only -> priced 85%/80%", "$169" in only and "$159" in only, only)
check("window-only detection", window_plans.job_is_window_cleaning_only(["window cleaning"], CFG))
check("bundled not 'only'", not window_plans.job_is_window_cleaning_only(["Window Cleaning", "Roof"], CFG))

# --- templates --------------------------------------------------------
print("templates")
job_dad = normalize_job(
    {"id": "j1", "service_type": ["Pressure Washing"], "price": 450, "date": "2026-09-01",
     "notes": "future quotes: roof wash $700, driveway pressure wash: $300",
     "customer": {"first_name": "Dad", "last_name": "Oneal"}}, {"first_name": "Dad", "last_name": "Oneal"})
ci = templates.render_check_in(job_dad, CFG)
check("check-in has first name", ci.startswith("Hey Dad"), ci)
co = templates.render_closeout(job_dad, CFG)
check("closeout has quotes", "Roof Wash: $700" in co["body"] and co["has_quotes"])
check("closeout no window block for PW-only", not co["has_window_block"])
check("closeout no triple blank lines", "\n\n\n" not in co["body"], repr(co["body"]))

job_bundled = normalize_job(
    {"id": "j2", "service_type": ["Pressure Washing", "Window Cleaning"], "price": 380,
     "date": "2026-09-01", "notes": "future quotes: house soft wash $650",
     "customer": {"first_name": "Jimmy", "last_name": "Alston"}}, None)
co2 = templates.render_closeout(job_bundled, CFG)
check("bundled closeout: quotes + window block", co2["has_quotes"] and co2["has_window_block"])
check("bundled window block: percentages only", "15% off" in co2["body"] and "20% off" in co2["body"] and "/visit" not in co2["body"], co2["body"])

job_nq = normalize_job(
    {"id": "j3", "service_type": ["Window Cleaning"], "price": 199, "date": "2026-09-01",
     "notes": "no future work", "customer": {"first_name": "Ann", "last_name": "W"}}, None)
co3 = templates.render_closeout(job_nq, CFG)
check("no-quotes closeout uses no_quotes template", not co3["has_quotes"])
check("no-quotes still has window block (priced)", co3["has_window_block"] and "$169" in co3["body"], co3["body"])
check("no-quotes closeout no triple blank", "\n\n\n" not in co3["body"])

esc = templates.render_escalation(
    {"customer_name": "Jane Doe", "customer_phone": "+12055550100",
     "service_label": "pressure washing", "date_label": "Sep 1"}, "it still looks dirty", CFG)
check("escalation has reply + name", "Jane Doe" in esc and "it still looks dirty" in esc, esc)

# --- timing ----------------------------------------------------------
print("timing")
# completes 10:00 -> +5h = 15:00, inside window -> unchanged
t1 = timing.compute_send_time(datetime(2026, 9, 1, 10, 0, tzinfo=CT), CFG)
check("inside window unchanged", t1.hour == 15 and t1.minute == 0, t1)
# completes 15:00 -> +5h = 20:00, after 19:00 -> next day 08:30
t2 = timing.compute_send_time(datetime(2026, 9, 1, 15, 0, tzinfo=CT), CFG)
check("after 7pm -> next day 08:30", t2.day == 2 and (t2.hour, t2.minute) == (8, 30), t2)
# completes 02:00 -> +5h = 07:00, before 08:30 -> same day 08:30
t3 = timing.compute_send_time(datetime(2026, 9, 1, 2, 0, tzinfo=CT), CFG)
check("before 8:30 -> same day 08:30", t3.day == 1 and (t3.hour, t3.minute) == (8, 30), t3)
# exactly 14:00 completion -> 19:00 boundary is allowed (== end, not >)
t4 = timing.compute_send_time(datetime(2026, 9, 1, 14, 0, tzinfo=CT), CFG)
check("14:00 -> 19:00 same day (boundary ok)", t4.day == 1 and t4.hour == 19, t4)
check("is_due true when now past", timing.is_due(t1, datetime(2026, 9, 1, 16, 0, tzinfo=CT), CFG))
check("is_due false when now before", not timing.is_due(t1, datetime(2026, 9, 1, 14, 0, tzinfo=CT), CFG))

# --- classify ------------------------------------------------------
print("classify")
check("positive -> SATISFIED", classify.classify_rulebased("Looks great, thank you!")[0] == "SATISFIED")
check("negative -> DISSATISFIED", classify.classify_rulebased("there are still streaks on the windows")[0] == "DISSATISFIED")
check("mixed -> UNCLEAR", classify.classify_rulebased("looks good but you missed a spot")[0] == "UNCLEAR")
check("vague -> UNCLEAR", classify.classify_rulebased("ok")[0] in ("SATISFIED", "UNCLEAR"))
check("empty -> UNCLEAR", classify.classify_rulebased("")[0] == "UNCLEAR")
check("normalize", classify.normalize("SATISFIED\n")[0] == "SATISFIED")
check("'call me' -> CONTACT_REQUEST", classify.classify_rulebased("Call me")[0] == "CONTACT_REQUEST")
check("'have someone call me' -> CONTACT_REQUEST",
      classify.classify_rulebased("can you have someone call me about the driveway")[0] == "CONTACT_REQUEST")
check("contact request beats positive words",
      classify.classify_rulebased("looks good, text me back about the gate")[0] == "CONTACT_REQUEST")
check("looks_like_contact_request helper", classify.looks_like_contact_request("please call me") == "call me")
check("no false positive on 'call'", classify.looks_like_contact_request("I'll call you if I see anything") is None)
check("normalize CONTACT_REQUEST", classify.normalize("CONTACT_REQUEST")[0] == "CONTACT_REQUEST")

# --- phone -------------------------------------------------------
print("phone normalization")
check("10 digit", normalize_phone("205-555-0142") == "+12055550142")
check("11 digit", normalize_phone("1 205 555 0142") == "+12055550142")
check("already e164", normalize_phone("+12055550142") == "+12055550142")

# --- scoping guardrails ------------------------------------------
print("scoping guardrails")
import copy as _copy
CFG_SCOPED = _copy.deepcopy(CFG)
CFG_SCOPED["sending"]["job_allowlist"] = ["keep-me"]
CFG_SCOPED["sending"]["completed_since"] = "2026-09-02"
with tempfile.TemporaryDirectory() as d:
    st = Store(Path(d) / "s.sqlite")
    js = [
        {"job_id": "keep-me", "completed": True, "customer_phone": "+12055550001", "first_name": "A",
         "service_label": "window cleaning", "date_label": "Sep 2", "service_type": ["Window Cleaning"]},
        {"job_id": "skip-me", "completed": True, "customer_phone": "+12055550002", "first_name": "B",
         "service_label": "pressure washing", "date_label": "Sep 2", "service_type": ["Pressure Washing"]},
    ]
    now = datetime(2026, 9, 3, 12, 0, tzinfo=CT)
    pipeline.plan_checkins(js, now, CFG_SCOPED, st)
    check("allowlist: tracked job created", st.get_followup("keep-me") is not None)
    check("allowlist: other job ignored", st.get_followup("skip-me") is None)
    # completed_since: same job seen before the floor date is not created
    st2 = Store(Path(d) / "s2.sqlite")
    pipeline.plan_checkins([js[0]], datetime(2026, 9, 1, 12, 0, tzinfo=CT), CFG_SCOPED, st2)
    check("completed_since: pre-floor job not created", st2.get_followup("keep-me") is None)

# --- closeout held when no future quotes -------------------------
print("closeout hold (no future quotes)")
CFG_HOLD = _copy.deepcopy(CFG)
CFG_HOLD["sending"]["job_allowlist"] = []
CFG_HOLD["sending"]["completed_since"] = None
CFG_HOLD["closeout"]["require_future_quotes"] = True
CFG_HOLD["closeout"]["hold_hours_before_alert"] = 24
with tempfile.TemporaryDirectory() as d:
    hs = Store(Path(d) / "h.sqlite")
    raw = {"id": "hold-job", "service_type": ["Pressure Washing"], "price": 300,
           "date": "2026-09-02", "completed": True,
           "notes": "Did the driveway and front walk. Gate code 1234.",
           "customer": {"first_name": "Pat", "last_name": "Lane"}}
    hjob = normalize_job(raw, {"first_name": "Pat", "last_name": "Lane", "phone": "+12055550009"})
    t0 = datetime(2026, 9, 2, 12, 0, tzinfo=CT)
    pipeline.plan_checkins([hjob], t0, CFG_HOLD, hs)
    t1 = t0 + __import__("datetime").timedelta(hours=6)
    for a in pipeline.plan_checkins([hjob], t1, CFG_HOLD, hs):
        if a.kind == "send_sms":
            pipeline.apply_checkin_sent(a.job_id, a.body, a.to, CFG_HOLD, hs)
    pipeline.handle_reply(hjob, "looks great thanks!", t1, CFG_HOLD, hs, classification="SATISFIED")
    t2 = t1 + __import__("datetime").timedelta(minutes=5)
    held = pipeline.plan_closeouts([hjob], t2, CFG_HOLD, hs)
    check("no-future-quotes closeout is held, not sent",
          all(x.kind != "send_sms" for x in held) and any(x.meta.get("held") for x in held),
          [x.as_dict() for x in held])
    check("held job still closeout_scheduled",
          hs.get_followup("hold-job")["status"] == "closeout_scheduled")
    # 25h later -> Anderson gets a one-time notes-needed alert
    t3 = t1 + __import__("datetime").timedelta(hours=25)
    late = pipeline.plan_closeouts([hjob], t3, CFG_HOLD, hs)
    alert = [x for x in late if x.stage == "notes_needed"]
    check("held >24h -> notes_needed alert to Anderson",
          len(alert) == 1 and alert[0].to == CFG["escalation"]["sms_to"], [x.as_dict() for x in late])
    pipeline.apply_notes_needed_alert_sent(alert[0].job_id, alert[0].body, alert[0].to, CFG_HOLD, hs)
    again = pipeline.plan_closeouts([hjob], t3 + __import__("datetime").timedelta(hours=1), CFG_HOLD, hs)
    check("notes_needed alert only fires once", not any(x.stage == "notes_needed" for x in again))
    # tech adds the future-quotes section -> closeout sends
    hjob["notes"] = raw["notes"] + " future quotes: roof wash $600"
    now_send = pipeline.plan_closeouts([hjob], t3, CFG_HOLD, hs)
    csend = [x for x in now_send if x.kind == "send_sms" and x.stage == "closeout"]
    check("closeout sends once notes updated", len(csend) == 1 and "Roof Wash: $600" in csend[0].body,
          [x.as_dict() for x in now_send])

# --- closeout sends without future quotes when not required -------
print("closeout without future quotes (require_future_quotes: false)")
CFG_NOREQ = _copy.deepcopy(CFG)
CFG_NOREQ["sending"]["job_allowlist"] = []
CFG_NOREQ["sending"]["completed_since"] = None
CFG_NOREQ["closeout"]["require_future_quotes"] = False
with tempfile.TemporaryDirectory() as d:
    ns = Store(Path(d) / "n.sqlite")
    raw = {"id": "noreq-job", "service_type": ["Pressure Washing"], "price": 300,
           "date": "2026-09-02", "completed": True,
           "notes": "Did the front and back. Gate code 5-5-5.",
           "customer": {"first_name": "sam", "last_name": "reed"}}
    njob = normalize_job(raw, {"first_name": "sam", "last_name": "reed", "phone": "+12055550021"})
    t0 = datetime(2026, 9, 2, 12, 0, tzinfo=CT)
    pipeline.plan_checkins([njob], t0, CFG_NOREQ, ns)
    t1 = t0 + __import__("datetime").timedelta(hours=6)
    for a in pipeline.plan_checkins([njob], t1, CFG_NOREQ, ns):
        if a.kind == "send_sms":
            pipeline.apply_checkin_sent(a.job_id, a.body, a.to, CFG_NOREQ, ns)
    pipeline.handle_reply(njob, "all good thanks", t1, CFG_NOREQ, ns, classification="SATISFIED")
    t2 = t1 + __import__("datetime").timedelta(minutes=5)
    out = pipeline.plan_closeouts([njob], t2, CFG_NOREQ, ns)
    csend = [x for x in out if x.kind == "send_sms" and x.stage == "closeout"]
    check("no-quotes closeout still sends", len(csend) == 1, [x.as_dict() for x in out])
    check("no-quotes closeout has referral + review, no quote lines",
          "refer a friend" in csend[0].body and "review" in csend[0].body
          and "- " not in csend[0].body, csend[0].body)
    check("name lowercased in record -> capitalized in greeting",
          normalize_job(raw, {"first_name": "sam"})["first_name"] == "Sam")

# --- contact request escalates immediately ----------------------
print("contact request -> immediate escalation")
CFG_CR = _copy.deepcopy(CFG)
CFG_CR["sending"]["job_allowlist"] = []
CFG_CR["sending"]["completed_since"] = None
with tempfile.TemporaryDirectory() as d:
    cs = Store(Path(d) / "c.sqlite")
    raw = {"id": "cr-job", "service_type": ["House Wash"], "price": 300, "date": "2026-09-02",
           "completed": True, "notes": "future quotes: roof wash $500",
           "customer": {"first_name": "Kim", "last_name": "Ray"}}
    cjob = normalize_job(raw, {"first_name": "Kim", "last_name": "Ray", "phone": "+12055550031"})
    t0 = datetime(2026, 9, 2, 12, 0, tzinfo=CT)
    pipeline.plan_checkins([cjob], t0, CFG_CR, cs)
    t1 = t0 + __import__("datetime").timedelta(hours=6)
    for a in pipeline.plan_checkins([cjob], t1, CFG_CR, cs):
        if a.kind == "send_sms":
            pipeline.apply_checkin_sent(a.job_id, a.body, a.to, CFG_CR, cs)
    # even with classification SATISFIED, "call me" wins
    out = pipeline.handle_reply(cjob, "Looks fine but call me about the back gate", t1, CFG_CR, cs,
                                classification="SATISFIED")
    check("contact request -> notify_anderson, not closeout",
          out[0].kind == "notify_anderson" and out[0].stage == "escalation", [x.as_dict() for x in out])
    check("contact request -> no clarify text sent",
          not any(x.stage == "clarify" for x in out))
    check("contact request body mentions the customer + reply",
          "Kim Ray" in out[0].body and "call me" in out[0].body.lower(), out[0].body)
    check("contact request -> to Anderson cell", out[0].to == CFG["escalation"]["sms_to"])
    pipeline.apply_escalation_sent(out[0].job_id, out[0].body, out[0].to, CFG_CR, cs)
    check("contact request -> status escalated",
          cs.get_followup("cr-job")["status"] == "escalated")
    clog = cs.db.execute("select classification from classification_log where job_id='cr-job'").fetchone()
    check("contact request logged as CONTACT_REQUEST", clog["classification"] == "CONTACT_REQUEST")

# --- pipeline end-to-end -----------------------------------------
print("pipeline end-to-end (temp db)")
CFG_E2E = _copy.deepcopy(CFG)
CFG_E2E["sending"]["job_allowlist"] = []
CFG_E2E["sending"]["completed_since"] = None
with tempfile.TemporaryDirectory() as d:
    store = Store(Path(d) / "t.sqlite")
    jobs = [normalize_job(j, j.get("customer_full")) for j in __import__("json").load(
        open(ROOT / "fixtures" / "sample_jobs.json"))]

    now = datetime(2026, 9, 1, 12, 0, tzinfo=CT)   # jobs "seen completed" now
    a0 = pipeline.plan_checkins(jobs, now, CFG_E2E, store)
    check("no checkins immediately (send_time in future)",
          all(x.kind != "send_sms" for x in a0), [x.as_dict() for x in a0])

    later = datetime(2026, 9, 1, 18, 0, tzinfo=CT)  # +5h -> 17:00, due
    a1 = pipeline.plan_checkins(jobs, later, CFG_E2E, store)
    sends = [x for x in a1 if x.kind == "send_sms"]
    check("4 check-ins now due", len(sends) == 4, [s.as_dict() for s in sends])
    for s in sends:
        pipeline.apply_checkin_sent(s.job_id, s.body, s.to, CFG_E2E, store)

    a2 = pipeline.plan_checkins(jobs, later, CFG_E2E, store)
    check("idempotent: no re-send", all(x.kind != "send_sms" for x in a2))

    dad = next(j for j in jobs if j["job_id"] == "test-dad-oneal")
    r1 = pipeline.handle_reply(dad, "Looks amazing, thank you!", later, CFG_E2E, store,
                               classification="SATISFIED")
    check("satisfied -> closeout scheduled (not sent)", r1[0].kind == "note" and r1[0].stage == "closeout")
    check("dad status closeout_scheduled", store.get_followup("test-dad-oneal")["status"] == "closeout_scheduled")
    # not due yet (2 min delay)
    c_early = pipeline.plan_closeouts(jobs, later, CFG_E2E, store)
    check("closeout not due at reply time", all(x.kind != "send_sms" for x in c_early), [x.as_dict() for x in c_early])
    # due after the delay
    after_delay = later + __import__("datetime").timedelta(minutes=3)
    c_due = pipeline.plan_closeouts(jobs, after_delay, CFG_E2E, store)
    csend = [x for x in c_due if x.kind == "send_sms" and x.stage == "closeout"]
    check("closeout due after delay", len(csend) == 1, [x.as_dict() for x in c_due])
    check("closeout carries quotes", "Roof Wash: $700" in csend[0].body, csend[0].body)
    pipeline.apply_closeout_sent(csend[0].job_id, csend[0].body, csend[0].to, CFG_E2E, store)
    check("dad status closed_satisfied", store.get_followup("test-dad-oneal")["status"] == "closed_satisfied")
    check("closeout idempotent", all(x.kind != "send_sms" for x in pipeline.plan_closeouts(jobs, after_delay, CFG_E2E, store)))

    bun = next(j for j in jobs if j["job_id"] == "test-bundled")
    r2 = pipeline.handle_reply(bun, "not happy, still dirty", later, CFG, store,
                               classification="DISSATISFIED")
    check("dissatisfied -> notify_anderson", r2[0].kind == "notify_anderson")
    check("escalation to Anderson cell", r2[0].to == CFG["escalation"]["sms_to"], r2[0].to)
    pipeline.apply_escalation_sent(r2[0].job_id, r2[0].body, r2[0].to, CFG, store)
    check("bundled status escalated", store.get_followup("test-bundled")["status"] == "escalated")

    win = next(j for j in jobs if j["job_id"] == "test-windows-only")
    r3 = pipeline.handle_reply(win, "hmm", later, CFG, store, classification="UNCLEAR")
    check("unclear -> clarify send", r3[0].stage == "clarify")
    pipeline.apply_clarify_sent(r3[0].job_id, r3[0].body, r3[0].to, CFG, store)
    r3b = pipeline.handle_reply(win, "still not sure what you mean", later, CFG, store,
                                classification="UNCLEAR")
    check("second unclear -> escalate", r3b[0].kind == "notify_anderson", [x.as_dict() for x in r3b])

    nq = next(j for j in jobs if j["job_id"] == "test-noquotes")
    r4 = pipeline.handle_reply(nq, "STOP", later, CFG, store)
    check("STOP -> opted_out", store.get_followup("test-noquotes")["status"] == "opted_out")
    check("STOP recorded in opt_outs", store.is_opted_out(nq["customer_phone"]))

    # classification + message logs populated
    clog = store.db.execute("SELECT COUNT(*) c FROM classification_log").fetchone()["c"]
    check("classifications logged", clog >= 4, clog)
    mlog = store.db.execute("SELECT COUNT(*) c FROM message_log WHERE direction='outbound'").fetchone()["c"]
    check("outbound messages logged", mlog >= 6, mlog)

print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
