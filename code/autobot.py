#!/usr/bin/env python3
"""
autobot.py - BotCouncil's autobot (BC thread, session BC6, 19 Oct 2026; BC7 changes marked "BC7").

Runs one small coding task:
  1. copies the files a task card names into a fresh run folder, with keys
     and passwords blanked out,
  2. starts a Docker box with no internet that can only see that copy,
  3. lets the worker AI (mini-swe-agent; DeepSeek on DeepInfra,
     a US host that keeps nothing) work in the box until it says it's done
     or hits a limit,
  4. runs the card's own check itself, in a fresh box (the AI never sees
     or touches the check),
  5. saves what changed, every step, and a readable report, then shows a
     desktop pop-up.
Nothing outside the run folder is ever changed.
BC8a (20 Oct 2026): the worker's box has the internet and a toolbox (box/Dockerfile, image autobot-box);
the check's box still has no internet. run --bare gives the old way, for comparison.

BC7 (19 Oct 2026):
  - A "no money left" or "bad key" answer from DeepInfra stops that job at once (no retrying) and every
    job still waiting in the batch is skipped. The reason is kept in STOPPED.txt; the next batch starts
    fresh.
  - When a batch finishes, a Nexus pop-up with sound says how it went (alerts.jsonl in ~/GUTS_private).
  - The failure notebook. Tony's words: "I bet if failures are documented our system can automatically
    refine how it works. Even if it needs me to do things via terminal. I like being in the loop anyhow."
    "I'll need a way to test it before saying yes, it can just link me to stuff and have me use the
    terminal". For every failed job the autobot drafts one new house rule (lessons.jsonl). Tony tries a
    lesson (the same job is run again with the lesson added), reads the report, then says yes or no.
    Only a yes adds it to house-rules.txt.
  - --repeat N runs every job N times, to see whether passes and fails hold up.

Use:
  autobot.py run TASK [TASK...] [--models pro,flash] [--parallel N] [--repeat N] [--skip-passed]
  autobot.py list                    recent runs
  autobot.py cost                    money spent
  autobot.py draft-lessons           draft a lesson for any failed run that has none yet
  autobot.py lessons                 lessons waiting for Tony, with the commands to try them
  autobot.py try-lesson N            run lesson N's job again with the lesson added
  autobot.py lesson N yes|no         yes adds it to the house rules; no sets it aside
"""
import argparse
import html
import json
import os
import re
import subprocess
import time
import traceback
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

BASE = Path.home() / "BC_private" / "autobot"
TASKS, RUNS = BASE / "tasks", BASE / "runs"
KEYFILE, RULES = BASE / "deepinfra-key.txt", BASE / "house-rules.txt"
STOP = BASE / "STOPPED.txt"                               # BC7
LESSONS = BASE / "lessons.jsonl"                          # BC7
ALERTS = Path.home() / "GUTS_private" / "alerts.jsonl"    # BC7: the Nexus pop-ups
IMAGE = "python:3.12-slim"
WORK_IMAGE = "autobot-box"   # BC8a: the worker's box: internet plus a toolbox (box/Dockerfile)
NO_NET = "There is no internet. "
TOOLS = ("You have the internet, for reading and installing only: never send the files in /work anywhere and never post to any site. Tools in this box: readweb URL (prints a web page as plain text; add a number for more characters, e.g. readweb URL 20000), curl, git, rg (fast search across files: rg -n 'words' /work), jq, python3 with requests and beautifulsoup4, and pip install --user for anything else. Useful pages: https://nexus.needpedia.org/llms.txt (what is on the Nexus) and https://nexus.needpedia.org/working-with-tony.txt (how this project works). Read only what the task needs. ")
MODELS = {"pro": "deepseek-ai/DeepSeek-V4-Pro-0813", "flash": "deepseek-ai/DeepSeek-V4-Flash-0731",
          "flash41": "deepseek-ai/DeepSeek-V4.1-Flash"}
API_URL = "https://api.deepinfra.com/v1/openai/chat/completions"
# Used only if DeepInfra doesn't say what a step cost: dollars per million word-pieces
# (read, written), set on the high side so the money caps stay safe.
FALLBACK_PRICES = {"pro": (1.50, 4.00), "flash": (0.30, 0.60), "flash41": (0.30, 0.90)}
LIMITS = {"steps": 30, "dollars": 0.50, "minutes": 20}
NOTES = "AUTOBOT-NOTES.txt"
EXIT_WORDS = {
    "Submitted": "said it was finished",
    "LimitsExceeded": "hit its step or money limit",
    "TimeExceeded": "hit its time limit",
    "RepeatedFormatError": "kept answering in the wrong format",
}
# BC7: words in an error answer that mean the money or the key is the problem, not the task.
STOP_RX = re.compile(r'balance|insufficient|payment|credit|billing|quota exceeded|spend limit|spending limit', re.I)

# Keys and passwords: a value assigned to a name containing PASS/KEY/TOKEN/SECRET,
# plus anything shaped like a known key, gets replaced with BLANKED.
NAME_RX = re.compile(
    r'^(\s*[A-Za-z_][A-Za-z0-9_]*(?:PASS|PASSWORD|KEY|TOKEN|SECRET)[A-Za-z0-9_]*\s*[:=]\s*)([\'"])(.*?)\2',
    re.I | re.M)
VALUE_RX = re.compile(r'(sk-or-v1-[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{24,}|ghp_[A-Za-z0-9]{20,}'
                      r'|github_pat_[A-Za-z0-9_]{20,}|AIza[0-9A-Za-z_-]{30,})')


def scrub(text):
    count = 0

    def name_sub(m):
        nonlocal count
        if not m.group(3) or m.group(3) == "BLANKED":
            return m.group(0)
        count += 1
        return f"{m.group(1)}{m.group(2)}BLANKED{m.group(2)}"

    text = NAME_RX.sub(name_sub, text)
    text, k = VALUE_RX.subn("BLANKED", text)
    return text, count + k


def parse_card(path):
    sections, cur = {}, None
    for line in path.read_text().splitlines():
        m = re.match(r'^==\s*([A-Z ]+?)\s*==\s*$', line)
        if m:
            cur = m.group(1).strip()
            sections[cur] = []
        elif cur:
            sections[cur].append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def card_files(card):
    out = []
    for line in card.get("FILES", "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        src, _, name = line.partition("->")
        src = Path(os.path.expanduser(src.strip()))
        out.append((src, name.strip() or src.name))
    return out


def card_limits(card):
    lim = dict(LIMITS)
    for line in card.get("LIMITS", "").splitlines():
        k, _, v = line.partition(":")
        k = k.strip().lower()
        if k in lim and v.strip():
            lim[k] = float(v) if k == "dollars" else int(v)
    return lim


def box(rd, script):
    """Run the check in a fresh box with no internet. It sees the AI's copy at /work and the
    untouched original at /before, both read-only. The AI never sees this check."""
    p = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "--user", f"{os.getuid()}:{os.getgid()}",
         "-e", "HOME=/tmp", "-v", f"{rd / 'work'}:/work:ro", "-v", f"{rd / 'before'}:/before:ro",
         "-w", "/tmp", IMAGE, "bash", "-c", script],
        capture_output=True, text=True, timeout=300)
    return p.returncode, (p.stdout + p.stderr)[-6000:]


def key_status():
    return "key: DeepInfra's billing page has the real total; the $10 limit is set on the key there"


def the_key():
    return KEYFILE.read_text().splitlines()[0].strip()


def stop_reason():
    try:
        return STOP.read_text().strip()
    except OSError:
        return ""


def money_or_key_problem(status, text):
    """BC7: True when an error answer means no money left or a bad key."""
    return status in (401, 402, 403) or (status >= 400 and bool(STOP_RX.search(text or "")))


def mark_stopped(status, text):
    why = "the key was refused" if status in (401, 403) else "no money left on DeepInfra, or the key's limit was reached"
    STOP.write_text(f"{time.strftime('%Y-%m-%d %H:%M')}  HTTP {status}: {why}\n{(text or '')[:300]}\n")
    return why


def write_status(rd, lim, response, prices):
    """BC7: a small note after every step, for the live view (abwatch.py): which step, money so far, and
    what the AI is doing right now."""
    try:
        sf = rd / "status.json"
        st = json.loads(sf.read_text()) if sf.exists() else {"step": 0, "cost": 0.0}
        u = response.get("usage") or {}
        c = u.get("estimated_cost") or u.get("cost") or \
            (u.get("prompt_tokens", 0) * prices[0] + u.get("completion_tokens", 0) * prices[1]) / 1e6
        text = (response.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        m = re.search(r"```[a-z_]*\n(.*?)```", text, re.S)
        if m:
            last = "running: " + " ".join(m.group(1).split())[:150]
        else:
            last = " ".join(text.split())[:150]
        st.update(step=st.get("step", 0) + 1, cost=round(st.get("cost", 0.0) + float(c), 5),
                  steps=lim["steps"], dollars=lim["dollars"], minutes=lim["minutes"], last=last,
                  words=st.get("words", 0) + u.get("prompt_tokens", 0) + u.get("completion_tokens", 0),
                  t=int(time.time()))
        sf.write_text(json.dumps(st))
    except Exception:
        pass


def task_text(card, extra_rule=None, bare=False):
    rules = RULES.read_text() if RULES.exists() else ""
    if extra_rule:   # BC7: a lesson being tried, added to a copy of the rules for this job only
        rules = rules.rstrip() + "\n- " + extra_rule + "\n"
    return (f"HOUSE RULES\n{rules}\n\nTASK\n{card.get('TASK', '')}\n\n"
            f"DONE WHEN\n{card.get('DONE WHEN', '')}\n\n"
            f"The files are in /work. {NO_NET if bare else TOOLS}Before you finish, write {NOTES} in /work: "
            "a few plain sentences on what you changed, how you checked it, and anything you're unsure of.")


def run_agent(card, lim, mkey, work, rd, res, extra_rule=None, bare=False):
    import yaml
    import minisweagent
    import requests
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.environments.docker import DockerEnvironment
    from minisweagent.models.openrouter_model import (OpenRouterAPIError, OpenRouterAuthenticationError,
                                                      OpenRouterRateLimitError)
    from minisweagent.models.openrouter_textbased_model import OpenRouterTextbasedModel

    key = the_key()
    prices = FALLBACK_PRICES.get(mkey, (1.50, 4.00))

    class DeepInfraModel(OpenRouterTextbasedModel):
        """The worker's text model, pointed at DeepInfra instead of OpenRouter."""
        # BC7: the worker retries every error up to 10 times except these. A money or key problem
        # is raised as OpenRouterAuthenticationError so it stops at once.
        abort_exceptions = [OpenRouterAuthenticationError, KeyboardInterrupt]

        def _query(self, messages, **kwargs):
            if STOP.exists():
                raise OpenRouterAuthenticationError("stopped: another job hit " + stop_reason().splitlines()[0])
            payload = {"model": self.config.model_name, "messages": messages, **(self.config.model_kwargs | kwargs)}
            r = requests.post(API_URL, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                              data=json.dumps(payload), timeout=300)
            if money_or_key_problem(r.status_code, r.text):
                why = mark_stopped(r.status_code, r.text)
                raise OpenRouterAuthenticationError(f"stopped: HTTP {r.status_code}: {why}")
            if r.status_code == 429:
                raise OpenRouterRateLimitError("Rate limit exceeded")
            if r.status_code >= 400:
                raise OpenRouterAPIError(f"HTTP {r.status_code}: {r.text[:500]}")
            d = r.json()
            write_status(rd, lim, d, prices)
            return d

        def _calculate_cost(self, response):
            u = response.get("usage") or {}
            cost = u.get("estimated_cost") or u.get("cost") or 0.0
            if not cost:
                cost = (u.get("prompt_tokens", 0) * prices[0] + u.get("completion_tokens", 0) * prices[1]) / 1e6
            return {"cost": float(cost)}

    cfg = yaml.safe_load((Path(minisweagent.__file__).parent / "config" / "mini_textbased.yaml").read_text()) or {}
    acfg, mcfg, ecfg = (dict(cfg.get(k) or {}) for k in ("agent", "model", "environment"))
    for d in (acfg, mcfg, ecfg):
        for k in ("agent_class", "model_class", "environment_class"):
            d.pop(k, None)
    acfg.update(step_limit=lim["steps"], cost_limit=lim["dollars"],
                wall_time_limit_seconds=lim["minutes"] * 60, output_path=rd / "steps.json")
    mcfg.update(model_name=MODELS[mkey], model_kwargs=dict(mcfg.get("model_kwargs") or {}))
    envv = dict(ecfg.get("env") or {})
    envv["HOME"] = "/tmp"
    net = ["--network", "none"] if bare else []   # BC8a: internet unless --bare
    env = DockerEnvironment(
        image=IMAGE if bare else WORK_IMAGE, cwd="/work", timeout=90, env=envv, container_timeout=f"{lim['minutes'] + 5}m",
        run_args=["--rm", *net, "--user", f"{os.getuid()}:{os.getgid()}", "-v", f"{work}:/work"])
    agent = DefaultAgent(DeepInfraModel(**mcfg), env, **acfg)
    try:
        agent.run(task_text(card, extra_rule, bare))
    except Exception as e:
        res["exit"] = f"crashed: {type(e).__name__}: {e}"[:400]
        (rd / "crash.txt").write_text(traceback.format_exc())
    finally:
        cid = getattr(env, "container_id", None)
        if cid:
            subprocess.run(["docker", "rm", "-f", cid], capture_output=True)
    return agent


def run_one(task, mkey, extra_rule=None, bare=False):
    if mkey == "auto":   # BC8d: the model picker
        return run_auto(task, extra_rule)
    if not bare:   # BC8b: jobs with tools go through run_tools; --bare keeps the old path below
        return run_tools(task, mkey, extra_rule)
    t0 = time.time()
    res = {"task": task, "model_key": mkey, "model": MODELS.get(mkey, mkey),
           "started": time.strftime("%Y-%m-%d %H:%M"), "steps": 0, "cost": 0.0, "mode": "bare" if bare else "tools"}
    if extra_rule:
        res["trial_rule"] = extra_rule
    # BC7: a money or key problem earlier in this batch: skip without starting anything.
    if STOP.exists():
        res.update(run="", minutes=0, check_passed=None, verdict="SKIPPED",
                   exit="skipped: " + (stop_reason().splitlines() or ["stopped"])[0])
        with open(RUNS / "index.jsonl", "a") as f:
            f.write(json.dumps(res) + "\n")
        return res
    rd = RUNS / f"{task}--{mkey}--{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}{'--trial' if extra_rule else ''}{'--bare' if bare else ''}"
    res["run"] = str(rd)
    try:
        card = parse_card(TASKS / task / "card.txt")
        res["title"] = card.get("TITLE", task)
        lim = card_limits(card)
        res["limits"] = lim
        before, work = rd / "before", rd / "work"
        before.mkdir(parents=True)
        work.mkdir()
        blanked = 0
        for src, name in card_files(card):
            text, n = scrub(src.read_text(errors="replace"))
            blanked += n
            for d in (before, work):
                (d / name).parent.mkdir(parents=True, exist_ok=True)
                (d / name).write_text(text)
        res["blanked"] = blanked

        agent = run_agent(card, lim, mkey, work, rd, res, extra_rule, bare)
        if agent.messages and "exit" not in res:
            res["exit"] = agent.messages[-1].get("extra", {}).get("exit_status", "")
        res["steps"], res["cost"] = agent.n_calls, round(agent.cost, 4)
        res["messages"] = agent.messages

        res["reference"] = card.get("REFERENCE", "")
        if "stopped:" in res.get("exit", ""):
            res["check_passed"], res["check_output"] = None, "(not checked: " + res["exit"] + ")"
            res["halt"] = "STOPPED"
        elif card.get("CHECK"):
            code, out = box(rd, card["CHECK"])
            res["check_passed"], res["check_output"] = code == 0, out
        else:
            res["check_passed"], res["check_output"] = None, "(this card has no check)"

        d = subprocess.run(["diff", "-ruN", "-x", NOTES, "before", "work"], cwd=rd, capture_output=True, text=True)
        (rd / "changes.diff").write_text(d.stdout)
        res["diff"] = d.stdout
        res["changed_lines"] = sum(1 for l in d.stdout.splitlines()
                                   if l[:1] in "+-" and l[:3] not in ("+++", "---"))
        notes = work / NOTES
        res["notes"] = notes.read_text()[:4000] if notes.exists() else "(the AI left no notes)"
    except Exception as e:
        res.setdefault("exit", f"crashed: {type(e).__name__}: {e}"[:400])
        rd.mkdir(parents=True, exist_ok=True)
        (rd / "crash.txt").write_text(traceback.format_exc())
        res.setdefault("check_passed", None)
    res["minutes"] = round((time.time() - t0) / 60, 1)
    res["verdict"] = res.pop("halt", None) or {True: "PASSED", False: "FAILED", None: "NO CHECK RUN"}[res.get("check_passed")]

    rd.mkdir(parents=True, exist_ok=True)
    (rd / "report.html").write_text(report_html(res))
    slim = {k: v for k, v in res.items() if k not in ("messages", "diff", "reference")}
    (rd / "report.json").write_text(json.dumps(slim, indent=2))
    with open(RUNS / "index.jsonl", "a") as f:
        f.write(json.dumps(slim) + "\n")
    notify(f"{task} [{mkey}]: {res['verdict']}, ${res['cost']}, {res['steps']} steps")
    return slim


# ===== BC8b: the tools (BC thread, session BC8, 20 Oct 2026) =====
# Tony: "Let's give them all the tools." "From now on we use logic to determine what will help AI."
# Only used when a job runs with tools (the default). run --bare still uses run_one's old path, unchanged.
PLAN_MODEL = "pro"
REVIEW_MODEL = "pro"
EASIER = BASE / "easier.jsonl"
SELF_CHECK = ("\n\nBEFORE YOU FINISH: for every line of DONE WHEN, write and run a quick test that proves it, and fix "
              "anything that fails. Where exact text matters, compare byte for byte. Keep your tests in /tmp, not "
              "/work, unless the task asks for them. The last line of your notes file starts with EASIER: and says "
              "in one sentence what would have made this job easier (a tool, a page, a clearer card), or "
              "EASIER: nothing.")


def fetch_reading(card, rd):
    """== READ == lines on a card: web addresses fetched fresh before the job, read-only at /reading."""
    rdir = rd / "reading"
    rdir.mkdir(exist_ok=True)
    urls = [l.strip() for l in card.get("READ", "").splitlines() if l.strip().startswith("http")]
    got = []
    for i, u in enumerate(urls[:8], 1):
        try:
            p = subprocess.run(["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp",
                                WORK_IMAGE, "readweb", u, "60000"], capture_output=True, text=True, timeout=120)
            out = p.stdout
        except Exception as e:
            out = f"could not fetch {u}: {e}"
        name = f"{i:02d}-" + re.sub(r'[^A-Za-z0-9.]+', '-', u.split('//', 1)[-1])[:60] + ".txt"
        (rdir / name).write_text(out)
        got.append(f"/reading/{name}  (from {u})")
    return got


def tools_text(card, extra_rule, reading, plan):
    t = task_text(card, extra_rule, bare=False) + SELF_CHECK
    if reading:
        t += "\n\nREAD FIRST: pages fetched for this job (read-only):\n" + "\n".join(reading)
    if plan:
        t += "\n\nA PLAN FROM A SENIOR AI (use it, but trust the files over the plan):\n" + plan
    return t


def fix_text(card, extra_rule, reading, feedback):
    return ("You are continuing a job. Your earlier work is already in /work, and your notes are in /work/"
            f"{NOTES}.\n\n" + tools_text(card, extra_rule, reading, "") +
            "\n\nWHAT NEEDS FIXING\n" + feedback +
            "\n\nFix only that and keep what already works. Then update the notes file, ending with the EASIER: line.")


def make_plan(card, work):
    outline = []
    for f in sorted(work.rglob("*")):
        if f.is_file():
            t = f.read_text(errors="replace")
            heads = [l.strip()[:120] for l in t.splitlines()
                     if re.match(r'\s*(# ---|def |class |function |async function |== |<script|</body)', l)][:40]
            outline.append(f"/work/{f.relative_to(work)}: {len(t.splitlines())} lines\n  " + "\n  ".join(heads))
    prompt = ("Plan a coding task for a cheaper AI worker that will do it in a sandbox with a terminal. Don't write "
              "the code. Give 5 to 10 short numbered steps: where to look (use the outline), what to change, and how "
              "to test each line of DONE WHEN before finishing. Point out the requirements that are easy to miss.\n\n"
              f"TASK\n{card.get('TASK', '')}\n\nDONE WHEN\n{card.get('DONE WHEN', '')}\n\n"
              "FILES (outline)\n" + "\n".join(outline)[:6000])
    return ask_model(PLAN_MODEL, prompt, max_tokens=2000)


def review(card, rd):
    diff = subprocess.run(["diff", "-ruN", "-x", NOTES, "before", "work"], cwd=rd, capture_output=True, text=True).stdout
    notes = (rd / "work" / NOTES).read_text()[:2000] if (rd / "work" / NOTES).exists() else "(no notes)"
    cut = len(diff) > 12000
    prompt = ("You review a change made by a worker AI before anyone else sees it. Compare it with the TASK and DONE "
              "WHEN, line by line. Look for: requirements missed or half done, things deleted or broken that should "
              "have stayed, and anything the task did not ask for. Don't nitpick style.\n"
              "If it is all right, answer with only: OK\n"
              "Otherwise answer PROBLEMS: then a short numbered list, each item something the worker can fix.\n\n"
              f"TASK\n{card.get('TASK', '')}\n\nDONE WHEN\n{card.get('DONE WHEN', '')}\n\n"
              f"THE WORKER'S NOTES\n{notes}\n\nTHE CHANGE{' (cut at 12000 characters)' if cut else ''}\n"
              f"{diff[:12000] or '(no files changed)'}")
    text, cost = ask_model(REVIEW_MODEL, prompt, max_tokens=2000)
    return text.strip().upper().startswith("OK"), text.strip(), cost


def exit_of(agent):
    if getattr(agent, "crash", ""):
        return agent.crash
    return agent.messages[-1].get("extra", {}).get("exit_status", "") if agent.messages else ""


def agent_round(prompt, lim, mkey, work, rd, out_name):
    """One round of the worker in the tools box (internet, toolbox, /reading). Same model wiring as run_agent."""
    import yaml
    import minisweagent
    import requests
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.environments.docker import DockerEnvironment
    from minisweagent.models.openrouter_model import (OpenRouterAPIError, OpenRouterAuthenticationError,
                                                      OpenRouterRateLimitError)
    from minisweagent.models.openrouter_textbased_model import OpenRouterTextbasedModel

    key = the_key()
    prices = FALLBACK_PRICES.get(mkey, (1.50, 4.00))

    class DeepInfraModel(OpenRouterTextbasedModel):
        abort_exceptions = [OpenRouterAuthenticationError, KeyboardInterrupt]

        def _query(self, messages, **kwargs):
            if STOP.exists():
                raise OpenRouterAuthenticationError("stopped: another job hit " + stop_reason().splitlines()[0])
            payload = {"model": self.config.model_name, "messages": messages, **(self.config.model_kwargs | kwargs)}
            r = requests.post(API_URL, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                              data=json.dumps(payload), timeout=300)
            if money_or_key_problem(r.status_code, r.text):
                why = mark_stopped(r.status_code, r.text)
                raise OpenRouterAuthenticationError(f"stopped: HTTP {r.status_code}: {why}")
            if r.status_code == 429:
                raise OpenRouterRateLimitError("Rate limit exceeded")
            if r.status_code >= 400:
                raise OpenRouterAPIError(f"HTTP {r.status_code}: {r.text[:500]}")
            d = r.json()
            write_status(rd, lim, d, prices)
            return d

        def _calculate_cost(self, response):
            u = response.get("usage") or {}
            cost = u.get("estimated_cost") or u.get("cost") or 0.0
            if not cost:
                cost = (u.get("prompt_tokens", 0) * prices[0] + u.get("completion_tokens", 0) * prices[1]) / 1e6
            return {"cost": float(cost)}

    cfg = yaml.safe_load((Path(minisweagent.__file__).parent / "config" / "mini_textbased.yaml").read_text()) or {}
    acfg, mcfg, ecfg = (dict(cfg.get(k) or {}) for k in ("agent", "model", "environment"))
    for d in (acfg, mcfg, ecfg):
        for k in ("agent_class", "model_class", "environment_class"):
            d.pop(k, None)
    acfg.update(step_limit=lim["steps"], cost_limit=lim["dollars"],
                wall_time_limit_seconds=lim["minutes"] * 60, output_path=rd / out_name)
    mcfg.update(model_name=MODELS[mkey], model_kwargs=dict(mcfg.get("model_kwargs") or {}))
    envv = dict(ecfg.get("env") or {})
    envv["HOME"] = "/tmp"
    env = DockerEnvironment(
        image=WORK_IMAGE, cwd="/work", timeout=90, env=envv, container_timeout=f"{lim['minutes'] + 5}m",
        run_args=["--rm", "--user", f"{os.getuid()}:{os.getgid()}", "-v", f"{work}:/work",
                  "-v", f"{rd / 'reading'}:/reading:ro"])
    agent = DefaultAgent(DeepInfraModel(**mcfg), env, **acfg)
    agent.crash = ""
    try:
        agent.run(prompt)
    except Exception as e:
        agent.crash = f"crashed: {type(e).__name__}: {e}"[:400]
        (rd / ("crash-" + out_name.replace(".json", ".txt"))).write_text(traceback.format_exc())
    finally:
        cid = getattr(env, "container_id", None)
        if cid:
            subprocess.run(["docker", "rm", "-f", cid], capture_output=True)
    return agent


def tools_html(r):
    w = {True: "passed", False: "failed", None: "not run"}
    rows = [("Kind of work", r.get("kind") or "(the card doesn't say)"),
            ("Check after the first round", w[r.get("check_first")]),
            ("The reviewer", "OK" if r.get("review_ok") else ("found problems" if "review_ok" in r else "not run")),
            ("Check after the review's fix", w[r.get("check_after_review")]),
            ("Check after the retry", w[r.get("check_after_retry")] if r.get("retried") else "no retry needed"),
            ("Helpers' cost (plan and review)", f"${r.get('helper_cost', 0)}"),
            ("What would have made it easier", r.get("easier", "(not said)"))]
    out = ["<h2>The tools</h2><table>" +
           "".join(f"<tr><td>{esc(a)}</td><td>{esc(b)}</td></tr>" for a, b in rows) + "</table>"]
    if r.get("reading"):
        out.append("<h2>Reading list</h2><pre>" + esc("\n".join(r["reading"])) + "</pre>")
    if r.get("plan"):
        out.append(f"<h2>The plan (from Pro)</h2><pre>{esc(r['plan'])}</pre>")
    if r.get("review"):
        out.append(f"<h2>The review (from Pro)</h2><pre>{esc(r['review'])}</pre>")
    return "\n".join(out)


def run_tools(task, mkey, extra_rule=None):
    t0 = time.time()
    res = {"task": task, "model_key": mkey, "model": MODELS.get(mkey, mkey),
           "started": time.strftime("%Y-%m-%d %H:%M"), "steps": 0, "cost": 0.0, "mode": "tools"}
    if extra_rule:
        res["trial_rule"] = extra_rule
    if STOP.exists():
        res.update(run="", minutes=0, check_passed=None, verdict="SKIPPED",
                   exit="skipped: " + (stop_reason().splitlines() or ["stopped"])[0])
        with open(RUNS / "index.jsonl", "a") as f:
            f.write(json.dumps(res) + "\n")
        return res
    rd = RUNS / f"{task}--{mkey}--{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}{'--trial' if extra_rule else ''}"
    res["run"] = str(rd)
    helper, msgs, agents = 0.0, [], []
    try:
        card = parse_card(TASKS / task / "card.txt")
        res["title"] = card.get("TITLE", task)
        res["kind"] = card.get("KIND", "").strip()
        res["reference"] = card.get("REFERENCE", "")
        lim = card_limits(card)
        res["limits"] = lim
        before, work = rd / "before", rd / "work"
        before.mkdir(parents=True)
        work.mkdir()
        blanked = 0
        for src, name in card_files(card):
            text, n = scrub(src.read_text(errors="replace"))
            blanked += n
            for d in (before, work):
                (d / name).parent.mkdir(parents=True, exist_ok=True)
                (d / name).write_text(text)
        res["blanked"] = blanked

        reading = fetch_reading(card, rd)
        res["reading"] = reading
        plan = ""
        if mkey != PLAN_MODEL:
            plan, c = make_plan(card, work)
            helper += c
            (rd / "plan.txt").write_text(plan)
        res["plan"] = plan

        def go(prompt, l, out_name, label):
            a = agent_round(prompt, l, mkey, work, rd, out_name)
            agents.append(a)
            if msgs:
                msgs.append({"role": "user", "content": f"----- {label} -----"})
                msgs.extend(a.messages[1:])
            else:
                msgs.extend(a.messages)
            e = exit_of(a)
            if "stopped:" in e:
                res["exit"] = e
            return e

        def check():
            if not card.get("CHECK"):
                return None, "(this card has no check)"
            code, out = box(rd, card["CHECK"])
            return code == 0, out

        res["exit"] = go(tools_text(card, extra_rule, reading, plan), lim, "steps.json", "first round")
        small = dict(lim, steps=max(8, lim["steps"] // 2), dollars=lim["dollars"] / 2,
                     minutes=max(5, lim["minutes"] // 2))
        if "stopped:" in res["exit"]:
            res["check_passed"], res["check_output"] = None, "(not checked: " + res["exit"] + ")"
            res["halt"] = "STOPPED"
        else:
            c, o = check()
            res["check_first"] = c
            ok, rtext, rc = review(card, rd)
            helper += rc
            res["review"], res["review_ok"] = rtext, ok
            (rd / "review.txt").write_text(rtext)
            if not ok and not res["exit"].startswith("crashed"):
                go(fix_text(card, extra_rule, reading,
                            "A reviewing AI read your change and found these problems:\n" + rtext),
                   small, "steps-review.json", "fix round after the review")
                c, o = check()
            res["check_after_review"] = c
            if c is False and "stopped:" not in res.get("exit", ""):
                go(fix_text(card, extra_rule, reading,
                            "An automatic check failed your work. The end of what it printed:\n" + o[-3000:]),
                   small, "steps-retry.json", "retry after the check failed")
                res["retried"] = True
                c, o = check()
            res["check_after_retry"] = c
            res["check_passed"], res["check_output"] = c, o
            if "stopped:" in res.get("exit", ""):
                res["halt"] = "STOPPED"

        d = subprocess.run(["diff", "-ruN", "-x", NOTES, "before", "work"], cwd=rd, capture_output=True, text=True)
        (rd / "changes.diff").write_text(d.stdout)
        res["diff"] = d.stdout
        res["changed_lines"] = sum(1 for l in d.stdout.splitlines()
                                   if l[:1] in "+-" and l[:3] not in ("+++", "---"))
        notes = work / NOTES
        res["notes"] = notes.read_text()[:4000] if notes.exists() else "(the AI left no notes)"
        m = re.search(r'(?im)^\s*EASIER:\s*(.+)$', res["notes"])
        if m:
            res["easier"] = m.group(1).strip()[:300]
            with open(EASIER, "a") as f:
                f.write(json.dumps({"t": res["started"], "task": task, "model_key": mkey,
                                    "easier": res["easier"], "run": str(rd)}) + "\n")
    except Exception as e:
        res.setdefault("exit", f"crashed: {type(e).__name__}: {e}"[:400])
        rd.mkdir(parents=True, exist_ok=True)
        (rd / "crash.txt").write_text(traceback.format_exc())
        res.setdefault("check_passed", None)
    res["steps"] = sum(a.n_calls for a in agents)
    res["cost"] = round(sum(a.cost for a in agents) + helper, 4)
    res["helper_cost"] = round(helper, 4)
    res["messages"] = msgs
    res["minutes"] = round((time.time() - t0) / 60, 1)
    res["verdict"] = res.pop("halt", None) or {True: "PASSED", False: "FAILED", None: "NO CHECK RUN"}[res.get("check_passed")]

    rd.mkdir(parents=True, exist_ok=True)
    (rd / "report.html").write_text(report_html(res).replace(
        "<h2>The AI's own notes</h2>", tools_html(res) + "\n<h2>The AI's own notes</h2>", 1))
    slim = {k: v for k, v in res.items() if k not in ("messages", "diff", "reference", "plan", "review")}
    (rd / "report.json").write_text(json.dumps(slim, indent=2))
    with open(RUNS / "index.jsonl", "a") as f:
        f.write(json.dumps(slim) + "\n")
    notify(f"{task} [{mkey}, tools]: {res['verdict']}, ${res['cost']}, {res['steps']} steps")
    return slim
# ===== end of BC8b =====


# ===== BC8d: the model picker and the batch status (BC thread, session BC8, 20 Oct 2026) =====
# Tony: "once we have a clear idea of what kinds of tasks different AI systems can do, we should setup the
# autobots to use that info ... to decide what AI systems will be used for what tasks." "It can't end up
# costing more than just using the stronger AI in the first place."
AUTO_LOG = RUNS / "auto.jsonl"
BATCH_STATUS = BASE / "batch-status.json"
PICK_SINCE = "2026-10-20 16:40"     # results from the BC8 measuring batch on (older nxcode results had a wrong check)
PICK_MIN_RATE = 0.75                # a way counts as proven when it passed at least 3 in 4
PICK_MIN_JOBS = 2
DEFAULT_LADDER = [("flash41", "tools"), ("flash", "tools"), ("pro", "tools")]


def card_kind(task):
    try:
        return parse_card(TASKS / task / "card.txt").get("KIND", "").strip()
    except OSError:
        return ""


def pick_ladder(task):
    """The order to try, cheapest proven way first, always ending with Pro with the tools."""
    kind = card_kind(task)
    idx = RUNS / "index.jsonl"
    rows = [json.loads(l) for l in idx.read_text().splitlines() if l.strip()] if idx.exists() else []
    stats = {}
    for r in rows:
        if (r.get("started", "") < PICK_SINCE or r.get("trial_rule")
                or r.get("verdict") not in ("PASSED", "FAILED") or r.get("model_key") not in MODELS):
            continue
        if (r.get("kind") or card_kind(r["task"])) != kind or not kind:
            continue
        s = stats.setdefault((r["model_key"], r.get("mode", "bare")), [0, 0, 0.0])
        s[0] += 1
        s[1] += r["verdict"] == "PASSED"
        s[2] += r.get("cost", 0)
    proven = sorted((c / ok, way) for way, (n, ok, c) in stats.items()
                    if n >= PICK_MIN_JOBS and ok and ok / n >= PICK_MIN_RATE)
    ladder = [way for _, way in proven] or list(DEFAULT_LADDER)
    if ("pro", "tools") in ladder:
        ladder.remove(("pro", "tools"))
    return kind, ladder + [("pro", "tools")]


def run_auto(task, extra_rule=None):
    kind, ladder = pick_ladder(task)
    tried, total, mins, steps, last = [], 0.0, 0.0, 0, {}
    for mkey, mode in ladder:
        last = run_one(task, mkey, extra_rule, bare=(mode == "bare"))
        tried.append(f"{mkey} {mode}: {last.get('verdict')} ${last.get('cost')}")
        total += last.get("cost", 0) or 0
        mins += last.get("minutes", 0) or 0
        steps += last.get("steps", 0) or 0
        if last.get("verdict") in ("PASSED", "STOPPED", "SKIPPED"):
            break
    res = dict(last)
    res.update(cost=round(total, 4), minutes=round(mins, 1), steps=steps, auto_ladder=tried, kind=kind,
               model_key=last.get("model_key", ladder[-1][0]))
    with open(AUTO_LOG, "a") as f:
        f.write(json.dumps({"started": time.strftime("%Y-%m-%d %H:%M"), "task": task, "kind": kind,
                            "plan": [f"{m} {md}" for m, md in ladder], "tried": tried,
                            "verdict": res.get("verdict"), "cost": res["cost"]}) + "\n")
    print(f"  {task}: tried {' -> '.join(tried)}", flush=True)
    return res


def batch_status(state, **kw):
    try:
        st = json.loads(BATCH_STATUS.read_text()) if BATCH_STATUS.exists() and state != "running" else {}
        st.update(kw, state=state)
        if state == "running":
            st.update(pid=os.getpid(), t0=time.time(), started=time.strftime("%Y-%m-%d %-I:%M %p"))
        else:
            st.update(ended=time.strftime("%-I:%M %p").lower(), t1=time.time())
        BATCH_STATUS.write_text(json.dumps(st))
    except Exception:
        pass


def start_watchdog(jobs, parallel, auto):
    """An urgent pop-up if the batch runs past twice its expected time. It only tells; it stops nothing."""
    import threading
    expected = sum(card_limits(parse_card(TASKS / t / "card.txt"))["minutes"] * 2 + 3 for t, _ in jobs)
    expected = expected / max(1, parallel) * (3 if auto else 1) + 15
    batch_status("running", jobs=len(jobs), expected_minutes=round(expected))

    def late():
        nexus_alert(f"Autobot batch running long ({time.strftime('%-I:%M %p').lower()})",
                    f"Started {round(expected)} minutes' worth of jobs over {round(expected)} minutes ago and is "
                    "still going. Live view: python3 ~/BC_private/autobot/abwatch.py", urgent=True)

    t = threading.Timer(expected * 60, late)
    t.daemon = True
    t.start()
    return t
# ===== end of BC8d =====


def notify(text):
    env = dict(os.environ)
    env.setdefault("DISPLAY", ":0")
    subprocess.run(["notify-send", "-a", "Nexus", "autobot", text], env=env, capture_output=True)


def nexus_alert(title, text, urgent=False):
    """BC7: a Nexus pop-up (read by ~/GUTS_private/guts_alerts.py), always with sound: it's Tony's own
    thing finishing, or stopping."""
    try:
        if ALERTS.parent.is_dir():
            with open(ALERTS, "a") as f:
                f.write(json.dumps({"t": int(time.time()), "u": "autobot", "level": "urgent" if urgent else "note",
                                    "title": title, "text": text, "link": "", "sound": True}) + "\n")
    except Exception:
        pass


CSS = """body{font:16px/1.55 system-ui,sans-serif;max-width:980px;margin:24px auto;padding:0 16px;
background:#16181d;color:#e6e6e6}h1{font-size:1.5em;margin:.2em 0}
h2{font-size:1.15em;margin-top:1.8em;border-bottom:1px solid #333;padding-bottom:4px}
.v{display:inline-block;padding:4px 12px;border-radius:6px;font-weight:700}.PASSED{background:#1f5f3a}
.FAILED{background:#7a2626}.none{background:#555}table{border-collapse:collapse}
td{padding:3px 16px 3px 0;vertical-align:top}td:first-child{color:#9ab}
pre{background:#0e1014;padding:10px;border-radius:6px;white-space:pre-wrap;word-break:break-word;font-size:13.5px}
.ai{border-left:3px solid #5b8def;padding-left:10px;margin:14px 0}.out{border-left:3px solid #666;padding-left:10px}
.who{font-weight:700;color:#9ab}"""


def esc(x):
    return html.escape(str(x))


def report_html(r):
    lim = r.get("limits", {})
    v = r.get("verdict", "")
    exitw = EXIT_WORDS.get(r.get("exit", ""), r.get("exit", ""))
    rows = [("Task", r.get("title", r["task"])), ("Model", f"{r['model']} on DeepInfra (nothing stored)"),
            ("The AI", exitw), ("Steps", f"{r.get('steps')} of {lim.get('steps', '?')}"),
            ("Cost", f"${r.get('cost')} of ${lim.get('dollars', '?')}"), ("Time", f"{r.get('minutes')} minutes"),
            ("Box", "internet and toolbox" if r.get("mode") == "tools" else "no internet (the old way)"), ("Lines changed", r.get("changed_lines", 0)), ("Keys blanked in the copy", r.get("blanked", 0)),
            ("Started", r.get("started")), ("Run folder", r.get("run"))]
    if r.get("trial_rule"):
        rows.insert(1, ("Lesson being tried", r["trial_rule"]))
    out = [f"<!doctype html><meta charset=utf-8><title>autobot: {esc(r['task'])}</title><style>{CSS}</style>",
           f"<h1>autobot: {esc(r.get('title', r['task']))}</h1>",
           f"<p><span class='v {esc(v) if v in ('PASSED', 'FAILED') else 'none'}'>{esc(v)}</span></p>",
           "<table>" + "".join(f"<tr><td>{esc(a)}</td><td>{esc(b)}</td></tr>" for a, b in rows) + "</table>",
           f"<h2>The AI's own notes</h2><pre>{esc(r.get('notes', ''))}</pre>",
           f"<h2>The check (run by the autobot, not the AI)</h2><pre>{esc(r.get('check_output', ''))}</pre>",
           f"<h2>What changed</h2><pre>{esc(r.get('diff', '') or '(nothing)')}</pre>",
           (f"<h2>What the thread actually did, for comparison</h2><pre>{esc(r['reference'])}</pre>"
            if r.get("reference") else ""),
           "<h2>Every step</h2>"]
    for i, m in enumerate(r.get("messages", [])):
        role = m.get("role", "")
        if role == "system" or i == 1:
            continue
        content = m.get("content", "")
        if not isinstance(content, str):
            content = json.dumps(content)
        content = content[:3000] + (" ...(cut)" if len(content) > 3000 else "")
        cls, who = ("ai", "AI:") if role == "assistant" else ("out", f"{role}:")
        out.append(f"<div class='{cls}'><span class='who'>{esc(who)}</span><pre>{esc(content)}</pre></div>")
    return "\n".join(out)


# ----- BC7: the failure notebook -----

LESSON_MODEL = "pro"   # drafting a lesson is a little reasoning, a few cents at most


def load_lessons():
    try:
        return [json.loads(l) for l in LESSONS.read_text().splitlines() if l.strip()]
    except OSError:
        return []


def save_lessons(ls):
    tmp = LESSONS.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(x) + "\n" for x in ls))
    tmp.replace(LESSONS)


def ask_model(mkey, prompt, max_tokens=600):
    import requests
    r = requests.post(API_URL, headers={"Authorization": f"Bearer {the_key()}", "Content-Type": "application/json"},
                      data=json.dumps({"model": MODELS[mkey], "messages": [{"role": "user", "content": prompt}],
                                       "max_tokens": max_tokens, "temperature": 0.2}), timeout=180)
    if money_or_key_problem(r.status_code, r.text):
        why = mark_stopped(r.status_code, r.text)
        raise RuntimeError("stopped: " + why)
    r.raise_for_status()
    d = r.json()
    u = d.get("usage") or {}
    p = FALLBACK_PRICES.get(mkey, (1.5, 4.0))
    cost = u.get("estimated_cost") or u.get("cost") or (u.get("prompt_tokens", 0) * p[0] + u.get("completion_tokens", 0) * p[1]) / 1e6
    text = d["choices"][0]["message"].get("content") or ""
    text = re.sub(r'(?s)<think>.*?</think>', '', text).strip()
    return text, float(cost)


def worth_a_lesson(x):
    """A real failure: the check ran and said no. Not a crash, a skip or a stop."""
    return (x.get("verdict") == "FAILED" and x.get("check_passed") is False and x.get("steps", 0) > 0
            and not str(x.get("exit", "")).startswith("crashed") and not x.get("trial_rule") and x.get("run"))


def draft_one(x, rules, known=""):
    rd = Path(x["run"])
    card = parse_card(TASKS / x["task"] / "card.txt")
    try:
        rep = json.loads((rd / "report.json").read_text())
    except Exception:
        rep = x
    try:
        diff = (rd / "changes.diff").read_text()[:4000]
    except OSError:
        diff = ""
    prompt = (
        "You help a coding robot learn from its mistakes. A worker AI did the task below inside a sandbox, "
        "and an automatic check (which the worker never saw) failed it.\n\n"
        f"HOUSE RULES THE WORKER ALREADY READS\n{rules}\n\n"
        f"LESSONS ALREADY DRAFTED, WAITING FOR TONY (numbered)\n{known or '(none)'}\n\n"
        f"TASK\n{card.get('TASK', '')}\n\nDONE WHEN\n{card.get('DONE WHEN', '')}\n\n"
        f"WHAT THE CHECK SAID\n{str(rep.get('check_output', ''))[-2500:]}\n\n"
        f"THE WORKER'S OWN NOTES\n{str(rep.get('notes', ''))[:2000]}\n\n"
        f"WHAT IT CHANGED (start of it)\n{diff}\n\n"
        "First decide whose fault it was. Compare what the check tested with what the TASK actually told the "
        "worker. If the check tested something the task didn't clearly ask for, or the check is wrong, answer "
        "with one line starting CARD PROBLEM: that says what's wrong.\n"
        "Otherwise write ONE new house rule: one or two plain-English sentences that would have prevented this "
        "failure and would also help on other tasks, so not about this particular file or task. Base it on "
        "what the change and the check actually show, not on guesses. Don't repeat a house rule. If one of the "
        "lessons already drafted covers it, answer with only SAME AS N (its number).\n"
        "Answer with only the rule, only the CARD PROBLEM line, or only SAME AS N.")
    text, cost = ask_model(LESSON_MODEL, prompt)
    text = re.sub(r'(?s)<think>.*?</think>', '', text)
    text = re.sub(r'(?s)^.*</think>', '', text).strip()      # a thinking part with no opening tag
    lines = [re.sub(r'^(new )?(house )?rule:\s*', '', l.strip(" -*\t"), flags=re.I)
             for l in text.splitlines() if l.strip()]
    lesson = " ".join(lines[:2])[:500]
    return lesson, cost


def draft_lessons(rows=None):
    """Draft a lesson for every real failure that doesn't have one yet."""
    idx = RUNS / "index.jsonl"
    if rows is None:
        rows = [json.loads(l) for l in idx.read_text().splitlines() if l.strip()] if idx.exists() else []
    ls = load_lessons()
    have = {x.get("run") for x in ls}
    todo = [x for x in rows if worth_a_lesson(x) and x["run"] not in have]
    if not todo:
        print("no new failures to learn from")
        return 0
    rules = RULES.read_text() if RULES.exists() else ""
    spent, made = 0.0, 0
    for x in todo:
        known = "\n".join(f"{l['id']}. {l['lesson']}" for l in ls
                          if l.get("status") in ("waiting", "approved") and not l["lesson"].upper().startswith("CARD PROBLEM"))
        try:
            lesson, cost = draft_one(x, rules, known)
        except Exception as e:
            print(f"could not draft a lesson for {x['task']} [{x['model_key']}]: {e}")
            if "stopped:" in str(e):
                break
            continue
        spent += cost
        same = re.match(r'^SAME AS\s*#?(\d+)', lesson, re.I)
        if same:   # already covered: remember the run so it isn't drafted again, but nothing new to review
            ls.append({"id": 0, "made": time.strftime("%Y-%m-%d %H:%M"), "task": x["task"], "model_key": x["model_key"],
                       "run": x["run"], "lesson": lesson, "status": "same as " + same.group(1), "trials": []})
            save_lessons(ls)
            print(f"{x['task']} [{x['model_key']}]: already covered by lesson {same.group(1)}")
            continue
        n = max([l.get("id", 0) for l in ls] + [0]) + 1
        ls.append({"id": n, "made": time.strftime("%Y-%m-%d %H:%M"), "task": x["task"], "model_key": x["model_key"],
                   "run": x["run"], "lesson": lesson, "status": "waiting", "trials": []})
        save_lessons(ls)
        made += 1
        print(f"lesson {n} drafted from {x['task']} [{x['model_key']}]: {lesson}")
    print(f"drafted {made} lesson(s), ${spent:.4f}")
    return made


def show_lessons():
    ls = [x for x in load_lessons() if x.get("status") == "waiting"]
    if not ls:
        print("no lessons waiting")
        return
    py = "~/BC_private/autobot/venv/bin/python ~/BC_private/autobot/autobot.py"
    for x in ls:
        print(f"\n===== LESSON {x['id']} (from {x['task']} [{x['model_key']}], {x['made']}) =====")
        print(x["lesson"])
        for t in x.get("trials", []):
            print(f"  tried {t['when']}: {t['verdict']}, ${t['cost']}  report: {t['run']}/report.html")
        print(f"  the failure it came from: {x['run']}/report.html")
        if x["lesson"].upper().startswith("CARD PROBLEM"):
            print(f"  this one is about the task card, not the worker: fix the card, then:  {py} lesson {x['id']} no")
        else:
            print(f"  try it:   {py} try-lesson {x['id']}")
            print(f"  then:     {py} lesson {x['id']} yes    (or no)")


def try_lesson(n):
    ls = load_lessons()
    x = next((l for l in ls if l.get("id") == n), None)
    if not x:
        print(f"no lesson {n}")
        return
    if x["lesson"].upper().startswith("CARD PROBLEM"):
        print("this one is about the task card, not the worker; nothing to try")
        return
    STOP.unlink(missing_ok=True)
    print(f"running {x['task']} [{x['model_key']}] again with lesson {n} added (this can take a few minutes)")
    res = run_one(x["task"], x["model_key"], extra_rule=x["lesson"])
    x.setdefault("trials", []).append({"when": time.strftime("%Y-%m-%d %H:%M"), "verdict": res.get("verdict"),
                                       "cost": res.get("cost"), "run": res.get("run")})
    save_lessons(ls)
    print(f"{res.get('verdict')}, ${res.get('cost')}, {res.get('steps')} steps")
    if res.get("run"):
        print(f"report: {res['run']}/report.html")
        print(f"open it:  xdg-open {res['run']}/report.html")


def decide_lesson(n, answer):
    ls = load_lessons()
    x = next((l for l in ls if l.get("id") == n), None)
    if not x:
        print(f"no lesson {n}")
        return
    if answer == "yes":
        if x["lesson"].upper().startswith("CARD PROBLEM"):
            print("this one is about the task card; it doesn't go in the house rules. Fix the card, then say no.")
            return
        rules = RULES.read_text() if RULES.exists() else ""
        if x["lesson"] in rules:
            print("already in the house rules, nothing added")
        else:
            with open(RULES, "a") as f:
                f.write(("" if rules.endswith("\n") or not rules else "\n") +
                        f"- {x['lesson']} (learned {time.strftime('%d %b %Y')} from {x['task']}, approved by Tony)\n")
            print("added to the house rules")
        x["status"] = "approved"
    else:
        x["status"] = "set aside"
        print(f"lesson {n} set aside; the house rules are unchanged")
    x["decided"] = time.strftime("%Y-%m-%d %H:%M")
    save_lessons(ls)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("tasks", nargs="+")
    r.add_argument("--models", default="pro")
    r.add_argument("--parallel", type=int, default=2, help="how many jobs at once (hosts limit how fast one key can ask)")
    r.add_argument("--repeat", type=int, default=1, help="run every job this many times")
    r.add_argument("--skip-passed", action="store_true", help="skip task+model pairs that already passed")
    r.add_argument("--bare", action="store_true", help="the old way: no internet, no toolbox (for comparison)")
    sub.add_parser("list")
    sub.add_parser("cost")
    sub.add_parser("draft-lessons")
    sub.add_parser("lessons")
    t = sub.add_parser("try-lesson")
    t.add_argument("n", type=int)
    d = sub.add_parser("lesson")
    d.add_argument("n", type=int)
    d.add_argument("answer", choices=["yes", "no"])
    a = ap.parse_args()
    RUNS.mkdir(parents=True, exist_ok=True)
    idx = RUNS / "index.jsonl"
    rows = [json.loads(l) for l in idx.read_text().splitlines() if l.strip()] if idx.exists() else []
    if a.cmd == "list":
        for x in rows[-20:]:
            print(f"{x.get('started')}  {x['task']} [{x['model_key']}, {x.get('mode', 'bare')}]{' (trying a lesson)' if x.get('trial_rule') else ''}: "
                  f"{x.get('verdict')}, ${x.get('cost')}")
        return
    if a.cmd == "cost":
        today, month = time.strftime("%Y-%m-%d"), time.strftime("%Y-%m")
        def total(f):
            return sum(x.get("cost", 0) for x in rows if f(x))
        print(f"runs: today ${total(lambda x: x.get('started', '').startswith(today)):.4f}, "
              f"this month ${total(lambda x: x.get('started', '').startswith(month)):.4f}, "
              f"all ${total(lambda x: True):.4f}  ({len(rows)} runs)")
        for k in MODELS:
            mine = [x for x in rows if x.get("model_key") == k and x.get("verdict") != "SKIPPED"]
            if mine:
                passed = sum(1 for x in mine if x.get("verdict") == "PASSED")
                print(f"  {k}: {len(mine)} runs, {passed} passed, ${total(lambda x: x.get('model_key') == k):.4f}")
        print(key_status())
        return
    if a.cmd == "draft-lessons":
        draft_lessons()
        return
    if a.cmd == "lessons":
        show_lessons()
        return
    if a.cmd == "try-lesson":
        try_lesson(a.n)
        return
    if a.cmd == "lesson":
        decide_lesson(a.n, a.answer)
        return
    keys = [m.strip() for m in a.models.split(",") if m.strip()]
    bad = [m for m in keys if m not in MODELS and m != "auto"]
    if bad:
        print(f"unknown model name(s): {bad}; use: {list(MODELS)} or auto")
        return
    missing = [t for t in a.tasks if not (TASKS / t / "card.txt").is_file()]
    if missing:
        print(f"no task card for: {missing}")
        return
    if not KEYFILE.is_file() or not KEYFILE.read_text().strip():
        print(f"no key in {KEYFILE}")
        return
    if STOP.exists():   # BC7: a new batch is a fresh try
        print("last batch stopped (" + stop_reason().splitlines()[0] + "); trying again")
        STOP.unlink(missing_ok=True)
    jobs = [(t, k) for t in a.tasks for k in keys]
    if a.skip_passed:
        done = {(x["task"], x["model_key"]) for x in rows if x.get("verdict") == "PASSED"
                and x.get("mode", "bare") == ("bare" if a.bare else "tools")}
        skipped = [j for j in jobs if j in done]
        jobs = [j for j in jobs if j not in done]
        for t, k in skipped:
            print(f"skipping {t} [{k}]: already passed")
    jobs = jobs * max(1, a.repeat)
    if not jobs:
        print("nothing left to run")
        return
    worst = sum(card_limits(parse_card(TASKS / t / "card.txt"))["dollars"] for t, _ in jobs)
    print(f"running {len(jobs)} jobs, {min(len(jobs), a.parallel)} at a time; "
          f"the most they can spend together is ${worst:.2f}")
    print(key_status())
    spent, results = 0.0, []
    watchdog = start_watchdog(jobs, a.parallel, "auto" in keys)   # BC8d
    with ProcessPoolExecutor(max_workers=max(1, min(len(jobs), a.parallel))) as ex:
        for res in ex.map(run_one, [j[0] for j in jobs], [j[1] for j in jobs], [None] * len(jobs),
                          [a.bare] * len(jobs)):
            results.append(res)
            spent += res.get("cost", 0)
            exitw = EXIT_WORDS.get(res.get("exit", ""), res.get("exit", ""))
            print(f"{res['task']} [{res['model_key']}]: {res['verdict']}, {res['steps']} steps, ${res['cost']}, "
                  f"{res['minutes']} min, the AI {exitw}\n  report: {res['run']}/report.html", flush=True)
    print(f"this batch spent ${spent:.4f}")
    print(key_status())
    # BC7: lessons from this batch's failures, then the Nexus pop-up
    made = 0
    if not STOP.exists():
        try:
            made = draft_lessons(results)
        except Exception as e:
            print(f"lessons not drafted: {e}")
    n = {v: sum(1 for x in results if x.get("verdict") == v) for v in ("PASSED", "FAILED", "STOPPED", "SKIPPED")}
    other = len(results) - sum(n.values())
    summary = (f"{n['PASSED']} passed, {n['FAILED']} failed" + (f", {other} other" if other else "") +
               f"; spent ${spent:.2f}." + (f" {made} new lesson(s) waiting: run autobot.py lessons." if made else ""))
    if STOP.exists():
        nexus_alert("Autobot stopped: no money left or the key was refused (" + time.strftime("%-I:%M %p").lower() + ")",
                    stop_reason().splitlines()[0] + "\n" + summary + f" {n['SKIPPED']} job(s) skipped.", urgent=True)
    else:
        nexus_alert("Autobot batch finished (" + time.strftime("%-I:%M %p").lower() + ")", summary)
    watchdog.cancel()   # BC8d
    batch_status("stopped" if STOP.exists() else "finished", summary=summary)


if __name__ == "__main__":
    main()
