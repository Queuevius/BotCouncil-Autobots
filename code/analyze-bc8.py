#!/usr/bin/env python3
# <<<<<<< analyze-bc8.py >>>>>>>
"""analyze-bc8.py - the autobot's numbers, for the GitHub post (BC thread, session BC8, 20 Oct 2026).
Reads ~/BC_private/autobot/runs/index.jsonl and the task cards. Changes nothing.
Prints the tables and also saves them to ~/BC_private/autobot/data-BC8.txt.
  python3 analyze-bc8.py            every real job (not the toy job, not lesson trials, not skipped)
  python3 analyze-bc8.py --since "2026-10-20 16:40"   only jobs started at or after that time
"""
import collections
import json
import re
import sys
from pathlib import Path

BASE = Path.home() / "BC_private" / "autobot"
since = sys.argv[sys.argv.index("--since") + 1] if "--since" in sys.argv else ""


def kind_of(task, cache={}):
    if task not in cache:
        k = ""
        try:
            t = (BASE / "tasks" / task / "card.txt").read_text()
            m = re.search(r"(?m)^== KIND ==\s*\n(.+)", t)
            k = m.group(1).strip() if m else ""
        except OSError:
            pass
        cache[task] = k or task
    return cache[task]


rows = [json.loads(l) for l in (BASE / "runs" / "index.jsonl").read_text().splitlines() if l.strip()]
rows = [r for r in rows if r.get("task") != "smoke-hello" and not r.get("trial_rule")
        and r.get("verdict") in ("PASSED", "FAILED") and r.get("started", "") >= since]
for r in rows:
    r["mode"] = r.get("mode", "bare")
    r["kind"] = r.get("kind") or kind_of(r["task"])
    r["ok"] = r["verdict"] == "PASSED"
    r["era"] = "BC8" if r.get("started", "") >= "2026-10-20 16:40" else "before BC8"

out = []


def p(s=""):
    out.append(s)


def pct(a, b):
    return f"{100 * a / b:.0f}%" if b else "-"


def line(label, rs):
    n, ok = len(rs), sum(r["ok"] for r in rs)
    cost = sum(r.get("cost", 0) for r in rs)
    mins = sum(r.get("minutes", 0) for r in rs)
    per_pass = f"${cost / ok:.4f}" if ok else "no passes"
    return (f"{label:<46} {ok:>3}/{n:<3} {pct(ok, n):>5}   ${cost / n:.4f}/job   {per_pass:>11}/pass   "
            f"{mins / n:.1f} min/job")


MODELS = ["pro", "flash", "flash41"]
p(f"AUTOBOT RESULTS{(' since ' + since) if since else ''}  ({len(rows)} real jobs)")
p("pro = DeepSeek V4 Pro (strong, pricier); flash = V4 Flash; flash41 = V4.1 Flash (both cheap). All on DeepInfra.")
p("'tools' = internet, toolbox, Pro's plan, self-test, Pro's review, one retry. 'bare' = none of those.")
p("Cost per pass = all money spent on those jobs divided by the jobs that passed.")

p("\n=== 1. BY MODEL, WITH AND WITHOUT THE TOOLS (BC8 batch only, same 7 jobs x 2 tries) ===")
bc8 = [r for r in rows if r["era"] == "BC8"]
for mode in ("bare", "tools"):
    for m in MODELS:
        rs = [r for r in bc8 if r["mode"] == mode and r["model_key"] == m]
        if rs:
            p(line(f"{mode:<6} {m}", rs))

p("\n=== 2. BY KIND OF WORK AND MODEL (BC8 batch: bare vs tools passes) ===")
kinds = sorted({r["kind"] for r in bc8})
for k in kinds:
    cells = []
    for m in MODELS:
        for mode in ("bare", "tools"):
            rs = [r for r in bc8 if r["kind"] == k and r["model_key"] == m and r["mode"] == mode]
            cells.append(f"{m} {mode} {sum(r['ok'] for r in rs)}/{len(rs)}")
    p(f"{k}:")
    p("    " + "   ".join(cells))

p("\n=== 3. WHAT EACH TOOLS STAGE ADDED (all jobs run with the tools) ===")
tools = [r for r in rows if r["mode"] == "tools" and "check_first" in r]
for m in MODELS + ["all"]:
    rs = tools if m == "all" else [r for r in tools if r["model_key"] == m]
    if not rs:
        continue
    first = sum(r.get("check_first") is True for r in rs)
    rev = sum(r.get("check_after_review") is True for r in rs)
    fin = sum(r.get("check_after_retry") is True for r in rs)
    flagged = sum(r.get("review_ok") is False for r in rs)
    false_alarm = sum(r.get("review_ok") is False and r.get("check_first") is True for r in rs)
    broke = sum(r.get("check_first") is True and r.get("check_after_review") is False for r in rs)
    retried = sum(bool(r.get("retried")) for r in rs)
    helper = sum(r.get("helper_cost", 0) for r in rs)
    total = sum(r.get("cost", 0) for r in rs)
    p(f"{m:<8} {len(rs)} jobs: passed after 1st round {first}, after review fix {rev}, after retry {fin}")
    p(f"         review found problems {flagged} times ({false_alarm} on work that already passed; "
      f"the fix broke passing work {broke} times); retries {retried}; plan+review cost "
      f"${helper:.4f} of ${total:.4f} ({pct(helper, total)})")

p("\n=== 4. THE WHOLE HISTORY, ALL ERAS (every real job ever run) ===")
for era in ("before BC8", "BC8"):
    for mode in ("bare", "tools"):
        for m in MODELS:
            rs = [r for r in rows if r["era"] == era and r["mode"] == mode and r["model_key"] == m]
            if rs:
                p(line(f"{era:<10} {mode:<6} {m}", rs))

p("\n=== 5. CHEAPEST WAY TO A PASS, BY KIND OF WORK (BC8 batch) ===")
p("For each kind: every model+mode with its pass rate and cost per pass, cheapest first.")
for k in kinds:
    opts = []
    for m in MODELS:
        for mode in ("bare", "tools"):
            rs = [r for r in bc8 if r["kind"] == k and r["model_key"] == m and r["mode"] == mode]
            ok = sum(r["ok"] for r in rs)
            if rs and ok:
                opts.append((sum(r.get("cost", 0) for r in rs) / ok, f"{m} {mode} {ok}/{len(rs)}"))
    opts.sort()
    p(f"{k}: " + ";  ".join(f"{label} ${c:.4f}/pass" for c, label in opts))

p("\n=== 6. FAILURES (BC8 batch) ===")
for r in bc8:
    if not r["ok"]:
        p(f"{r['started']}  {r['task']} [{r['model_key']}, {r['mode']}]  {str(r.get('exit', ''))[:60]}  {r.get('run', '')}")

p("\n=== 7. WHAT WORKERS SAID WOULD HAVE MADE THE JOB EASIER ===")
easier = BASE / "easier.jsonl"
if easier.exists():
    es = [json.loads(l) for l in easier.read_text().splitlines() if l.strip()]
    es = [e for e in es if not re.match(r"(?i)^nothing\b", e.get("easier", "").strip())]
    for e in es:
        p(f"- {e['task']} [{e['model_key']}]: {e['easier'][:200]}")
    if not es:
        p("(every worker said nothing)")
else:
    p("(none yet)")

text = "\n".join(out)
print(text)
(BASE / "data-BC8.txt").write_text(text + "\n")
