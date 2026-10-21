#!/usr/bin/env python3
"""bot_runner.py - runs one browser job inside the autobot-browser box (BC thread, session BC8, 20 Oct 2026).
Reads /work/job.json: {"task", "model", "max_steps", "minutes"}. The key comes in the DEEPINFRA_KEY setting,
never in a file. If /work/site exists, the practice job site runs at http://127.0.0.1:8765/ and records what the
browser sends in /work/out/. Files in /work/upload/ are the ones the browser may upload.
Writes /work/out/result.json (final answer, steps, word-pieces, errors) and /work/out/history.json."""
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

W = Path("/work")
OUT = W / "out"
OUT.mkdir(exist_ok=True)
job = json.loads((W / "job.json").read_text())
res = {"started": time.time(), "final_result": None, "done": False, "steps": 0, "errors": [],
       "prompt_tokens": 0, "completion_tokens": 0}

server = None
if (W / "site").is_dir():
    server = subprocess.Popen([sys.executable, "/opt/bbot/practice_server.py", str(W / "site"), str(OUT), "8765"])
    for _ in range(50):
        try:
            import urllib.request
            urllib.request.urlopen("http://127.0.0.1:8765/", timeout=1)
            break
        except Exception:
            time.sleep(0.2)


async def main():
    from browser_use import Agent, Browser
    from browser_use.llm.deepseek.chat import ChatDeepSeek
    class CountingDeepSeek(ChatDeepSeek):
        """browser-use's DeepSeek wrapper drops the word-piece counts; this keeps them, for the cost."""
        def _client(self):
            c = super()._client()
            orig = c.chat.completions.create

            async def create(*a, **kw):
                r = await orig(*a, **kw)
                u = getattr(r, "usage", None)
                if u:
                    res["prompt_tokens"] += u.prompt_tokens or 0
                    res["completion_tokens"] += u.completion_tokens or 0
                return r
            c.chat.completions.create = create
            return c

    llm = CountingDeepSeek(model=job["model"], api_key=os.environ["DEEPINFRA_KEY"],
                           base_url="https://api.deepinfra.com/v1/openai")
    browser = Browser(headless=True, executable_path="/usr/bin/chromium", chromium_sandbox=False,
                      enable_default_extensions=False, user_data_dir="/tmp/browser-profile")
    uploads = [str(p) for p in sorted((W / "upload").glob("*"))] if (W / "upload").is_dir() else []
    agent = Agent(task=job["task"], llm=llm, browser=browser, use_vision=False, use_judge=False,
                  available_file_paths=uploads, max_failures=3, enable_signal_handler=False,
                  include_tool_call_examples=bool(job.get("examples")))   # BC8g: worked examples of the answer shape
    try:
        history = await asyncio.wait_for(agent.run(max_steps=int(job.get("max_steps", 30))),
                                         timeout=60 * int(job.get("minutes", 15)))
        res.update(final_result=history.final_result(), done=bool(history.is_done()),
                   success=history.is_successful(), steps=history.number_of_steps(),
                   urls=[u for u in history.urls() if u][-10:],
                   errors=[e for e in history.errors() if e][-5:])
        try:
            history.save_to_file(str(OUT / "history.json"))
        except Exception as e:
            res["errors"].append(f"history not saved: {e}")
    except asyncio.TimeoutError:
        res["errors"].append("hit its time limit")
    except Exception as e:
        res["errors"].append(f"crashed: {type(e).__name__}: {e}"[:400])
    finally:
        try:
            await browser.kill()
        except Exception:
            pass


try:
    asyncio.run(main())
finally:
    res["seconds"] = round(time.time() - res["started"])
    (OUT / "result.json").write_text(json.dumps(res, indent=1, default=str))
    if server:
        server.terminate()
