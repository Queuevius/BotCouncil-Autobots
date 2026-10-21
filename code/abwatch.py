#!/usr/bin/env python3
"""
abwatch.py - a live view of the autobot (BC thread, session BC7, 19 Oct 2026).

Tony's words: "I was hoping for something that would have like, loading bars or a running token count. or
activity window of some kind. What's it workin on, for instance?"

Redraws every 5 seconds in the terminal (Ctrl+C closes it; the autobot keeps running). Shows:
  - whether a batch is running, and how many of its jobs are done
  - each job working right now: task, model, how long, and (from BC7's autobot on) a bar for its steps
    and its money, word-pieces used, and what the AI is doing this moment
  - each finished job: passed or failed, cost, steps, minutes
  - money spent so far in this batch
Only reads files; changes nothing.
  python3 abwatch.py          live
  python3 abwatch.py --once   print once and stop
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

BASE = Path.home() / "BC_private" / "autobot"
RUNS = BASE / "runs"
NAME_RX = re.compile(r"^(.+?)--([a-z0-9]+)--(\d{8}-\d{6})")


def bar(part, whole, width=20):
    try:
        f = max(0.0, min(1.0, float(part) / float(whole)))
    except (TypeError, ZeroDivisionError, ValueError):
        f = 0.0
    n = int(round(f * width))
    return "[" + "#" * n + "." * (width - n) + "]"


def newest_log():
    logs = sorted(BASE.glob("batch-*.log"), key=lambda p: p.stat().st_mtime)
    return logs[-1] if logs else None


def batch_info():
    log = newest_log()
    try:   # BC8d: the autobot writes batch-status.json
        bst = json.loads((BASE / "batch-status.json").read_text())
    except Exception:
        bst = {}
    total, started = 0, None
    if log:
        txt = log.read_text(errors="replace")
        m = re.search(r"running (\d+) jobs", txt)
        if m:
            total = int(m.group(1))
        started = log.stat().st_ctime
    running = subprocess.run(["pgrep", "-f", "autobot.py run"], capture_output=True).returncode == 0
    if bst:
        total, started = bst.get("jobs", total), bst.get("t0", started)
        running = bst.get("state") == "running" and os.path.exists(f"/proc/{bst.get('pid')}")
    return log, total, started, running, bst


def mins(sec):
    sec = int(max(0, sec))
    return f"{sec // 60}m{sec % 60:02d}s"


def draw():
    log, total, started, running, bst = batch_info()
    now = time.time()
    since = (started or now) - 120
    active, done = [], []
    for rd in RUNS.iterdir() if RUNS.exists() else []:
        if not rd.is_dir():
            continue
        try:
            made = rd.stat().st_ctime
        except OSError:
            continue
        if made < since:
            continue
        m = NAME_RX.match(rd.name)
        task, model = (m.group(1), m.group(2)) if m else (rd.name, "?")
        if "--trial" in rd.name:
            task += " (trying a lesson)"
        rep = rd / "report.json"
        if rep.exists():
            try:
                r = json.loads(rep.read_text())
            except Exception:
                r = {}
            done.append((rep.stat().st_mtime, task, model, r))
        else:
            st = {}
            try:
                st = json.loads((rd / "status.json").read_text())
            except Exception:
                pass
            active.append((made, task, model, st))
    out = []
    head = "AUTOBOT  " + time.strftime("%-I:%M:%S %p").lower()
    out.append(head)
    out.append("=" * 72)
    if running:
        out.append(f"RUNNING   {len(done)} of {total or '?'} jobs done   {bar(len(done), total or 1, 30)}")
    else:
        if bst.get("ended"):
            out.append(f"FINISHED at {bst['ended']}   {bst.get('summary', '')}")
        else:
            out.append(f"NOT RUNNING   last batch: {len(done)} of {total or '?'} jobs done")
    spent = sum(float(r.get("cost") or 0) for _, _, _, r in done) + sum(float(s.get("cost") or 0) for _, _, _, s in active)
    out.append(f"spent so far this batch: ${spent:.4f}" + (f"   (log: {log.name})" if log else ""))
    out.append("")
    out.append(f"WORKING NOW ({len(active)})")
    if not active:
        out.append("  nothing")
    for made, task, model, st in sorted(active):
        out.append(f"  {task} [{model}]   {mins(now - made)}")
        if st:
            out.append(f"    steps {bar(st.get('step'), st.get('steps'))} {st.get('step')} of {st.get('steps')}"
                       f"     money {bar(st.get('cost'), st.get('dollars'), 12)} ${st.get('cost'):.4f} of ${st.get('dollars')}")
            out.append(f"    word-pieces used: {st.get('words', 0):,}")
            out.append(f"    now: {st.get('last', '')[:110]}")
        else:
            out.append("    (no step details: this job started before the live view existed)")
    out.append("")
    out.append(f"FINISHED ({len(done)}, newest first)")
    for t, task, model, r in sorted(done, reverse=True)[:16]:
        v = r.get("verdict", "?")
        mark = {"PASSED": "ok  ", "FAILED": "FAIL", "STOPPED": "STOP", "SKIPPED": "skip"}.get(v, "??  ")
        out.append(f"  {mark} {task} [{model}]  ${r.get('cost')}  {r.get('steps')} steps  {r.get('minutes')} min")
    if len(done) > 16:
        out.append(f"  ...and {len(done) - 16} more")
    out.append("")
    out.append("Ctrl+C closes this view; the autobot keeps running.")
    return "\n".join(out)


def main():
    if "--once" in sys.argv:
        print(draw())
        return
    try:
        while True:
            text = draw()
            sys.stdout.write("\033[2J\033[H" + text + "\n")
            sys.stdout.flush()
            time.sleep(5)
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
