#!/bin/bash
# <<<<<<< measure-bc8.sh >>>>>>>
# BC8's measuring batch (BC thread, 20 Oct 2026): 7 kinds of work x 3 models x 2 tries,
# first with the tools, then the old way (--bare). Stops by itself if part B isn't in,
# if the tiny job fails with the tools, or if DeepInfra says no money or a bad key.
cd ~/BC_private/autobot || exit 1
PY=~/BC_private/autobot/venv/bin/python
AB=~/BC_private/autobot/autobot.py
SHOW='\]: (PASSED|FAILED|SKIPPED|STOPPED|NO CHECK)|spent|^running|lesson|STOPPED|crash'
grep -q BC8b "$AB" || { echo "STOPPED: part B (install-bc8b.py) isn't in autobot.py yet"; exit 1; }
echo "== $(date '+%F %T')  the tiny job with the tools =="
$PY $AB run smoke-hello --models flash 2>&1 | grep -E "$SHOW"
last=$(ls -td runs/smoke-hello--flash--* | head -1)
if ! grep -q '"check_first"' "$last/report.json" || ! grep -q '"verdict": "PASSED"' "$last/report.json"; then
  echo "STOPPED: the tiny job didn't pass with the tools. Its report: $last/report.html"; exit 1
fi
TASKS="rename-route bc5d-replay nxcode pa-trim bugfix read-report two-file"
echo "== $(date '+%F %T')  with the tools: 42 jobs, 4 at a time =="
$PY $AB run $TASKS --models pro,flash,flash41 --repeat 2 --parallel 4 2>&1 | grep -E "$SHOW"
[ -f STOPPED.txt ] && { echo "STOPPED: $(head -1 STOPPED.txt)"; exit 1; }
echo "== $(date '+%F %T')  the old way (--bare): 42 jobs, 4 at a time =="
$PY $AB run $TASKS --models pro,flash,flash41 --repeat 2 --parallel 4 --bare 2>&1 | grep -E "$SHOW"
echo "== $(date '+%F %T')  all done =="
