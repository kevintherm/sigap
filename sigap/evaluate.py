"""Runs the 48-question golden set in both languages through the full chat pipeline and
writes a grading sheet (PRD: Evaluation and testing).

    SIGAP_NOW=2026-10-04T19:00 uv run sigap-eval
"""
import csv
import os
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALL_COURSES = ["IF2101", "SI2203", "SI2105", "MA2102", "SI2207", "UM2001"]


def main():
    import argparse
    ap = argparse.ArgumentParser(prog="sigap-eval")
    ap.add_argument("--agent", action="store_true", help="send questions through the Gemini agent flow (LLM mode)")
    ap.add_argument("--limit", type=int, default=0, help="only the first N questions per set (saves LLM quota)")
    ap.add_argument("--set", choices=["policy", "dates", "trap", "reminder"], help="only this question set")
    opts = ap.parse_args()
    os.environ.setdefault("SIGAP_NOW", "2026-10-04T19:00")
    # Never write evaluation tickets or calendar files into the real outbox.
    os.environ["SIGAP_OUTBOX_DIR"] = tempfile.mkdtemp(prefix="sigap-eval-")
    from . import agent  # imported after env is set
    from .langflow_client import backend_name, tool_backend

    backend = tool_backend()
    real = {name: getattr(backend, name) for name in
            ("get_my_deadlines", "get_study_period", "answer_campus_policy", "create_study_reminder")}
    captured = {}

    def spy(name):
        def wrapper(*a, **k):
            captured[name] = real[name](*a, **k)
            return captured[name]
        return wrapper

    for name in real:
        setattr(backend, name, spy(name))

    rows = list(csv.DictReader(open(ROOT / "eval" / "golden_set.csv", encoding="utf-8")))
    if opts.set:
        rows = [r for r in rows if r["set"] == opts.set]
    if opts.limit:
        rows = [r for k in ("policy", "dates", "trap", "reminder") for r in [x for x in rows if x["set"] == k][:opts.limit]]
    out = []
    for r in rows:
        for lang in ("id", "en"):
            q = r[f"question_{lang}"]
            captured.clear()
            t0 = time.perf_counter()
            # The demo student, enrolled in all six courses (what the backend would send for Dinda).
            session = agent.Session(lang=lang, linked=True, courses=ALL_COURSES)
            if opts.agent:
                steps = []

                def run(text, sid, courses, linked, _steps=steps):
                    out = backend.run_agent(text, sid, courses, linked)
                    _steps.extend(out["steps"])
                    return out
                res = agent.handle_with_agent(session, q, run)
                for st in steps:  # grade on what the tools really returned
                    if st["result"]:
                        captured[st["tool"]] = st["result"]
            else:
                res = agent.handle(session, q)
            ms = (time.perf_counter() - t0) * 1000
            called = [c["tool"] for c in res["tool_calls"]]
            ok, cited, handed_off, got = grade(r["set"], r["expected"], captured, called)
            out.append({
                "qid": r["qid"], "set": r["set"], "language": lang, "question": q, "expected": r["expected"],
                "tools_called": " > ".join(called), "got": got, "correct": int(ok), "cited": int(cited),
                "handed_off": int(handed_off), "reply_language": res["language"], "ms": round(ms, 1),
            })

    results = ROOT / "eval" / "results"
    results.mkdir(parents=True, exist_ok=True)
    path = results / f"grading_{backend_name()}{'_agent' if opts.agent else ''}_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)

    agg = defaultdict(lambda: [0, 0])
    for o in out:
        for key in (o["set"], f"{o['set']}/{o['language']}", "all"):
            agg[key][0] += o["correct"]
            agg[key][1] += 1
    mode = f"agent ({backend.agent_model()} via Langflow)" if opts.agent else "rules"
    print(f"Golden set @ SIGAP_NOW={os.environ['SIGAP_NOW']} · tools backend: {backend_name()} · understanding: {mode}\n")
    for key in ("policy", "policy/id", "policy/en", "dates", "dates/id", "dates/en", "trap", "trap/id", "trap/en",
                "reminder", "reminder/id", "reminder/en", "all"):
        if key not in agg:
            continue
        c, n = agg[key]
        print(f"  {key:<11} {c:>3}/{n:<3} {100 * c / n:5.1f}%")
    lang_ok = sum(o["reply_language"] == o["language"] for o in out)
    print(f"\n  reply in student's language: {lang_ok}/{len(out)}")
    print(f"  max time per question: {max(o['ms'] for o in out):.0f} ms")
    fails = [o for o in out if not o["correct"]]
    if fails:
        print("\nFailures:")
        for o in fails:
            print(f"  {o['qid']}/{o['language']}: {o['question']}\n      expected {o['expected']}, got {o['got']} via [{o['tools_called']}]")
    print(f"\nGrading sheet: {path.relative_to(ROOT)}")
    sys.exit(0 if not fails else 1)


def grade(kind, expected, captured, called):
    if kind == "policy":
        r = captured.get("answer_campus_policy")
        if not r:
            return False, False, False, "no policy lookup"
        if r["status"] != "FOUND":
            return False, False, "escalate_to_student_services" in called, "NOT_FOUND"
        sec = r["citation"]["section"]
        return sec == expected, True, False, f"§{sec}"
    if kind == "trap":
        r = captured.get("answer_campus_policy")
        handed = "escalate_to_student_services" in called
        if r and r["status"] == "FOUND":
            return False, True, handed, f"answered from §{r['citation']['section']}"
        if not r:
            return False, False, handed, "no policy lookup: " + ",".join(called)
        return handed, False, handed, "NOT_FOUND + handoff offered" if handed else "NOT_FOUND, no handoff"
    if kind == "reminder":
        # expected: at=<exact reminder time offered> | plan (default 3-day plan) | invalid=<reason>
        r = captured.get("create_study_reminder")
        if not r:
            return False, False, False, "no reminder tool call: " + ",".join(called)
        if r["status"] == "INVALID_TIME":
            got = f"invalid={r['reason']}"
        elif r["status"] == "NEEDS_CONFIRMATION" and r.get("remind_at"):
            got = f"at={r['remind_at'][:16]}"
        elif r["status"] == "NEEDS_CONFIRMATION":
            got = "plan"
        else:
            got = r["status"]
        return got == expected, False, False, f"{r['item']['item_id']} {got}"
    # dates
    key, _, val = expected.partition("=")
    if key in ("items", "first"):
        r = captured.get("get_my_deadlines")
        if not r:
            return False, False, False, "no deadline lookup: " + ",".join(called)
        ids = [i["item_id"] for i in r["items"]]
        ok = ids == val.split("|") if key == "items" else (ids[:1] == [val])
        return ok, True, False, "|".join(ids) or "(none)"
    r = captured.get("get_study_period")
    if not r:
        return False, False, False, "no period lookup: " + ",".join(called)
    if key == "week":
        return str(r["week"]) == val, True, False, f"week {r['week']}"
    tkey, status, end = val.split(":")
    t = r["topic"] or {}
    got = f"{t.get('key')}:{t.get('status')}:{t.get('end')}"
    return got == val, True, False, got


if __name__ == "__main__":
    main()
