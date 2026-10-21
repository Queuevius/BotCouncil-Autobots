#!/usr/bin/env python3
"""
bbot.py - the browser autobot (BC thread, session BC8, 20 Oct 2026).

A browser autobot does a job on websites: a hidden Chromium browser in its own Docker box (autobot-browser),
driven by browser-use with the same DeepSeek models on DeepInfra. It reads pages as text (DeepSeek can't see
images). Practice jobs run against a practice job site inside the box, and each job is graded by a check the
AI never sees, in a separate offline box, the same way as the coding jobs.

Tony's words (20 Oct 2026): "Okay, I'm open to whatever browser tool works best."

A browser card has the usual sections (TITLE, KIND, TASK, DONE WHEN, CHECK, LIMITS) plus:
  == SITE ==    a folder copied into the box and served at http://127.0.0.1:8765/ (practice jobs)
  == UPLOAD ==  files the browser may upload (one path per line)
What the practice site receives lands in /work/out/ (submit-N.json, draft.json, uploads/); the AI's final
answer is in /work/out/result.json. The check reads those.

Use:
  bbot.py test                                    no AI, no cost: is the browser box working?
  bbot.py run TASK [TASK...] [--models flash41,pro] [--parallel 2] [--repeat N]
  bbot.py results                                 every browser job so far, by card and model
Every run goes in runs/ with a readable report.html and a line in runs/index.jsonl (mode "browser").
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

BASE = Path.home() / "BC_private" / "autobot"
sys.path.insert(0, str(BASE))
import autobot as ab  # noqa: E402

IMAGE = "autobot-browser"
MONEY_RX = re.compile(r"\b(401|402|403)\b.*?(auth|key|balance|payment|insufficient|credit|quota)", re.I | re.S)


def paths(card, key):
    return [Path(os.path.expanduser(l.strip())) for l in card.get(key, "").splitlines()
            if l.strip() and not l.strip().startswith("#")]


def run_one(task, mkey, examples=False):
    t0 = time.time()
    res = {"task": task, "model_key": mkey, "model": ab.MODELS.get(mkey, mkey),
           "mode": "browser-examples" if examples else "browser",
           "started": time.strftime("%Y-%m-%d %H:%M"), "steps": 0, "cost": 0.0}
    if ab.STOP.exists():
        res.update(run="", minutes=0, check_passed=None, verdict="SKIPPED",
                   exit="skipped: " + (ab.stop_reason().splitlines() or ["stopped"])[0])
        with open(ab.RUNS / "index.jsonl", "a") as f:
            f.write(json.dumps(res) + "\n")
        return res
    rd = ab.RUNS / f"{task}--{mkey}--{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}--browser{'-examples' if examples else ''}"
    res["run"] = str(rd)
    received = ""
    try:
        card = ab.parse_card(ab.TASKS / task / "card.txt")
        res["title"] = card.get("TITLE", task)
        res["kind"] = card.get("KIND", "").strip()
        lim = ab.card_limits(card)
        res["limits"] = lim
        work = rd / "work"
        (rd / "before").mkdir(parents=True)
        (work / "out").mkdir(parents=True)
        for d in paths(card, "SITE"):
            shutil.copytree(d, work / "site", dirs_exist_ok=True)
        ups = paths(card, "UPLOAD")
        if ups:
            (work / "upload").mkdir()
            for f in ups:
                shutil.copy2(f, work / "upload" / f.name)
        (work / "job.json").write_text(json.dumps({"task": card.get("TASK", ""), "model": ab.MODELS[mkey],
                                                   "max_steps": lim["steps"], "minutes": lim["minutes"],
                                                   "examples": examples}))
        env = dict(os.environ, DEEPINFRA_KEY=ab.the_key())
        cmd = ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp",
               "-e", "DEEPINFRA_KEY", "--shm-size=1g", "-v", f"{work}:/work", IMAGE,
               "python3", "/opt/bbot/bot_runner.py"]
        try:
            p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=60 * (lim["minutes"] + 5))
            log = p.stdout + p.stderr
        except subprocess.TimeoutExpired as e:
            log = f"the box ran past its time limit and was stopped\n{e}"
        (rd / "box-log.txt").write_text(log[-300000:])
        rf = work / "out" / "result.json"
        r = json.loads(rf.read_text()) if rf.exists() else {"errors": ["the box left no result"]}
        res["steps"] = r.get("steps", 0)
        pr = ab.FALLBACK_PRICES.get(mkey, (1.5, 4.0))   # priced from word-pieces at the high-side rates
        res["words"] = (r.get("prompt_tokens") or 0) + (r.get("completion_tokens") or 0)
        res["cost"] = round(((r.get("prompt_tokens") or 0) * pr[0] + (r.get("completion_tokens") or 0) * pr[1]) / 1e6, 4)
        res["final_result"] = r.get("final_result")
        res["errors"] = r.get("errors", [])
        res["exit"] = "said it was finished" if r.get("done") else ("; ".join(res["errors"][-1:]) or "did not finish")
        if MONEY_RX.search(json.dumps(res["errors"]) + log[-4000:]):
            ab.mark_stopped(402, json.dumps(res["errors"])[:300])
            res["halt"] = "STOPPED"
        if card.get("CHECK") and "halt" not in res:
            code, out = ab.box(rd, card["CHECK"])
            res["check_passed"], res["check_output"] = code == 0, out
        else:
            res["check_passed"], res["check_output"] = None, "(not checked)"
        for f in sorted((work / "out").glob("*.json")):
            if f.name not in ("result.json", "history.json"):
                received += f"--- {f.name}\n{f.read_text()[:3000]}\n"
    except Exception as e:
        res.setdefault("exit", f"crashed: {type(e).__name__}: {e}"[:400])
        rd.mkdir(parents=True, exist_ok=True)
        (rd / "crash.txt").write_text(traceback.format_exc())
        res.setdefault("check_passed", None)
    res["minutes"] = round((time.time() - t0) / 60, 1)
    res["verdict"] = res.pop("halt", None) or {True: "PASSED", False: "FAILED", None: "NO CHECK RUN"}[res.get("check_passed")]
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "report.html").write_text(report_html(res, received, rd))
    (rd / "report.json").write_text(json.dumps(res, indent=2, default=str))
    with open(ab.RUNS / "index.jsonl", "a") as f:
        f.write(json.dumps(res, default=str) + "\n")
    try:
        ab.notify(f"{task} [{mkey}, browser]: {res['verdict']}, ${res['cost']}")
    except Exception:
        pass
    return res


def report_html(r, received, rd):
    esc = ab.esc
    v = r.get("verdict", "")
    lim = r.get("limits", {})
    rows = [("Task", r.get("title", r["task"])), ("Kind of work", r.get("kind", "")),
            ("Model", f"{r['model']} on DeepInfra (nothing stored), driving a hidden browser"),
            ("The AI", r.get("exit", "")), ("Steps", f"{r.get('steps')} of {lim.get('steps', '?')}"),
            ("Cost (from word-pieces, high-side rates)", f"${r.get('cost')}"), ("Time", f"{r.get('minutes')} minutes"),
            ("Started", r.get("started")), ("Run folder", r.get("run"))]
    try:
        log = (rd / "box-log.txt").read_text().splitlines()
    except OSError:
        log = []
    keep = [l for l in log if re.search(r"Step \d|Eval|Memory|Next goal|▶️|Action|done|Final|ERROR|WARNING", l)][-200:]
    out = [f"<!doctype html><meta charset=utf-8><title>browser autobot: {esc(r['task'])}</title><style>{ab.CSS}</style>",
           f"<h1>browser autobot: {esc(r.get('title', r['task']))}</h1>",
           f"<p><span class='v {esc(v) if v in ('PASSED', 'FAILED') else 'none'}'>{esc(v)}</span></p>",
           "<table>" + "".join(f"<tr><td>{esc(a)}</td><td>{esc(b)}</td></tr>" for a, b in rows) + "</table>",
           f"<h2>The AI's final answer</h2><pre>{esc(r.get('final_result'))}</pre>",
           f"<h2>The check (run by the autobot, not the AI)</h2><pre>{esc(r.get('check_output', ''))}</pre>",
           f"<h2>What the site received</h2><pre>{esc(received or '(nothing)')}</pre>",
           f"<h2>Errors</h2><pre>{esc(chr(10).join(map(str, r.get('errors', []))) or '(none)')}</pre>",
           f"<h2>What it did, step by step (from the box's log)</h2><pre>{esc(chr(10).join(keep) or '(no log)')}</pre>"]
    return "\n".join(out)


def selftest():
    site = ab.TASKS / "browser-practice-site"
    cmd = ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp", "--shm-size=1g",
           "-v", f"{site}:/site:ro", IMAGE, "bash", "-c",
           "python3 /opt/bbot/practice_server.py /site /tmp/out 8765 & sleep 1; "
           "chromium --headless=new --no-sandbox --disable-gpu --dump-dom http://127.0.0.1:8765/ 2>/dev/null "
           "| grep -o '<h1>[^<]*</h1>'; python3 -c 'import browser_use; print(\"browser-use is installed\")'"]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    out = (p.stdout + p.stderr).strip()
    ok = "Riverbend" in out and "browser-use is installed" in out
    print(out[-800:])
    print("BROWSER BOX WORKS" if ok else "BROWSER BOX TEST FAILED")


def results():
    idx = ab.RUNS / "index.jsonl"
    rows = [json.loads(l) for l in idx.read_text().splitlines() if l.strip()] if idx.exists() else []
    rows = [r for r in rows if str(r.get("mode", "")).startswith("browser") and r.get("verdict") in ("PASSED", "FAILED")]
    if not rows:
        print("no browser jobs yet")
        return
    keys = sorted({(r["task"], r["model_key"], r["mode"]) for r in rows})
    for t, m, md in keys:
        rs = [r for r in rows if r["task"] == t and r["model_key"] == m and r["mode"] == md]
        ok = sum(r["verdict"] == "PASSED" for r in rs)
        priced = [r for r in rs if r.get("words")]   # the first practice run (20 Oct, 10:22 pm) had no word count
        okp = sum(r["verdict"] == "PASSED" for r in priced)
        cost = sum(r.get("cost", 0) for r in priced)
        money = (f"${cost / len(priced):.4f}/job   " + (f"${cost / okp:.4f}/pass" if okp else "no priced passes")
                 if priced else "cost not measured")
        print(f"{t:<14} {m:<8} {'+examples' if md.endswith('examples') else '':<9} {ok}/{len(rs)} passed   {money}   "
              f"{sum(r.get('minutes', 0) for r in rs) / len(rs):.1f} min/job")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("tasks", nargs="+")
    r.add_argument("--models", default="flash41")
    r.add_argument("--parallel", type=int, default=2)
    r.add_argument("--repeat", type=int, default=1)
    r.add_argument("--examples", action="store_true", help="show the AI worked examples of the answer shape")
    sub.add_parser("test")
    sub.add_parser("results")
    a = ap.parse_args()
    ab.RUNS.mkdir(parents=True, exist_ok=True)
    if a.cmd == "test":
        return selftest()
    if a.cmd == "results":
        return results()
    keys = [m.strip() for m in a.models.split(",") if m.strip()]
    bad = [m for m in keys if m not in ab.MODELS]
    if bad:
        print(f"unknown model name(s): {bad}; use: {list(ab.MODELS)}")
        return
    missing = [t for t in a.tasks if not (ab.TASKS / t / "card.txt").is_file()]
    if missing:
        print(f"no task card for: {missing}")
        return
    if ab.STOP.exists():
        print("last batch stopped (" + ab.stop_reason().splitlines()[0] + "); trying again")
        ab.STOP.unlink(missing_ok=True)
    jobs = [(t, k) for t in a.tasks for k in keys] * max(1, a.repeat)
    print(f"running {len(jobs)} browser jobs, {min(len(jobs), a.parallel)} at a time")
    watchdog = ab.start_watchdog(jobs, a.parallel, False)
    spent, results_ = 0.0, []
    with ProcessPoolExecutor(max_workers=max(1, min(len(jobs), a.parallel))) as ex:
        for res in ex.map(run_one, [j[0] for j in jobs], [j[1] for j in jobs], [a.examples] * len(jobs)):
            results_.append(res)
            spent += res.get("cost", 0)
            print(f"{res['task']} [{res['model_key']}, {res.get('mode')}]: {res['verdict']}, {res.get('steps')} steps, "
                  f"${res['cost']}, {res.get('minutes')} min, the AI {res.get('exit', '')}\n"
                  f"  report: {res.get('run')}/report.html", flush=True)
    n = {v: sum(1 for x in results_ if x.get("verdict") == v) for v in ("PASSED", "FAILED", "STOPPED", "SKIPPED")}
    summary = f"{n['PASSED']} passed, {n['FAILED']} failed; spent about ${spent:.2f}."
    print(summary)
    when = time.strftime("%-I:%M %p").lower()
    if ab.STOP.exists():
        ab.nexus_alert(f"Browser autobot stopped: no money left or the key was refused ({when})", summary, urgent=True)
    else:
        ab.nexus_alert(f"Browser autobot batch finished ({when})", summary)
    watchdog.cancel()
    ab.batch_status("stopped" if ab.STOP.exists() else "finished", summary=summary)


if __name__ == "__main__":
    main()
