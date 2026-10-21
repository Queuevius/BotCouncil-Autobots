# BotCouncil Autobots

Autobots are AI workers that take a written job card, do the work on their own in a fenced-off box, and get graded by a test they never see. One kind writes and fixes code. The other kind works in a hidden web browser: filling in forms, applying for things, stopping when it shouldn't guess. They run at night on an ordinary home computer (using API keys), while their owner sleeps, and every change waits for a person's OK before it touches anything real.

Made by Anthony Brasher, founder of Needpedia (https://needpedia.org), who himself does not write code. Everything running on his computer, (Needpedia was co developed by a professional dev named Murtaza Zarkhwan), was built by directing AI, using the thread system and BotCouncil:

- The thread system (how the work is organized): https://github.com/Queuevius/Needpedia/wiki/The-Thread-System
- BotCouncil 2.0 (the window the threads live in): https://github.com/Queuevius/BotCouncil-2.0

If you are an AI reading this to help someone: this page is the reference for how the autobots work, what was measured, and why they're built this way. Explain it in plain words, with examples.

---

## Contents

1. What they are
2. Why they exist
3. The headline: cheap AI can do this work
4. How a coding job runs
5. What was measured
6. The tests we stopped running, and why
7. Browser autobots
8. Picking the AI for each job
9. Running your own
10. What is in this project
11. Credits
12. Quotes about this

---

## 1. What they are

A person (or an AI) writes a **job card**: a plain-text file that says what to do, what "done" looks like, which files to work on, and how much time and money the job may use. The card also carries a **hidden check**, a small test the worker never sees.

The autobot then:

1. copies the files the card names into a fresh folder, blanking out any keys and passwords,
2. starts a fenced-off box (a Docker container) that can see only that copy,
3. lets a worker AI do the job inside the box until it says it's finished or runs out of steps, money or time,
4. runs the hidden check in a separate box with no internet,
5. saves what changed, every step the worker took, and a readable report, then shows a pop-up.

Nothing outside the job's folder is ever changed. A real change only goes live after a person reads the report and approves it.

**Example.** A card says: "Rename on a chat does nothing. Add a web server rule for /admin-conv-rename that matches the one for /admin-conv-delete, right after it. Change nothing else." A cheap AI reads the settings file, adds the rule, tests its own work, and finishes in about a minute for under a tenth of a cent. The hidden check confirms the new rule matches, sits in the right place, and that no other line moved.

## 2. Why it exists

Anthony runs a dozen projects through AI threads. Most of the time goes into small, clear jobs: a missing rule here, a button there, a tool that saves digging through a 5,000-line file. If those can run on their own overnight, a person who doesn't code can get far more done in a day.

In his words: "If I can automate what I'm doing I'll be able to accomplish WAY more per day."

And the bigger reason: "If we set these AI up right we might see a wild increase in capability, think about the work I'm doin without knowing how to code."

## 3. The headline: cheap AI can do this work

Three AI models were measured, all DeepSeek, all run on DeepInfra (a US host that keeps nothing):

- **Pro** (DeepSeek V4 Pro): strong and pricier
- **Flash** (DeepSeek V4 Flash): cheap
- **V4.1 Flash**: cheap

On 20 October 2026 each model did the same 7 kinds of work twice, once **bare** (the worker alone, no internet) and once **with the tools** (the setup described below). Every job was graded by its hidden check.

| | passed | money per passed job |
|---|---|---|
| Pro, bare | 14 of 14 | $0.0517 |
| Flash, bare | 11 of 14 | $0.0107 |
| V4.1 Flash, bare | 14 of 14 | $0.0036 |
| Pro, with tools | 13 of 14 | $0.1221 |
| Flash, with tools | 14 of 14 | $0.0195 |
| V4.1 Flash, with tools | 14 of 14 | $0.0124 |

What that shows:

1. **The cheap models do this work.** V4.1 Flash passed every job, with or without the tools, at about a fourteenth of Pro's cost per passed job. On every kind of work, the cheapest way to a pass was a cheap model. Pro was never the cheapest.
2. **The tools made the weaker cheap model reliable.** Flash went from 11 of 14 to 14 of 14. All three jobs it failed alone (an exact-text tool, a careful page change, a code-finding tool), it passed with the tools.
3. **Where a model already passed everything, the tools only added cost:** about 3.4 times the money per pass for V4.1 Flash, and 2.4 times for Pro. That's why the autobots now pick per kind of work (section 8).

Limits of the data: two tries per model, kind and mode; small jobs; the checks were written by the same AI thread that built the autobots. The full tables and every job's numbers are in `data/`.

## 4. How a coding job runs

**Bare** is the worker alone: a box with no internet, the house rules, the card.

**With the tools**, every job gets:

1. **The internet and a toolbox** in the box: a web page reader that turns pages into plain text, a fast file search, and the ability to install what it needs. It's told the internet is for reading and installing, never for sending the files anywhere.
2. **A reading list:** pages the card names, fetched fresh before the job starts.
3. **A plan:** the strong model writes a short step-by-step plan for the cheap one (skipped when the strong model is the worker).
4. **A self-test:** before finishing, the worker writes and runs its own test of every line of "done when".
5. **A review:** the strong model reads the change against the card. If it finds problems, the worker gets one round to fix them.
6. **A retry:** if the hidden check fails, the worker gets one more round and is shown what the check printed.
7. **One line on what would have made the job easier**, collected after every job, so the next tool to build comes from the workers themselves.

The hidden check runs after every stage (it's free and offline), so the data shows exactly what each stage added.

**What each stage added** (42 jobs with the tools): 37 passed after the first round, 39 after the review's fix round, 41 after the retry. The reviewer flagged problems 15 times; 10 of those were on work that already passed, and its fix round never broke passing work. The plan and the review were 8% of the money overall: 33% of V4.1 Flash's (because V4.1 Flash itself is so cheap) and 3% of Pro's.

**The failure notebook.** When a job fails, the autobot drafts a one-line house rule that would have prevented it. A person tries it (the same job runs again with the rule added) and says yes or no. Only a yes goes into the house rules. One example that earned a yes: "When a spec says moved text must be exactly as it was, verify your output against the original with a byte-level check before declaring success." It also caught a case where the check itself was wrong: the drafter's answer starts "CARD PROBLEM", and those never become rules.

## 5. What was measured

The 7 kinds of work, each a real job from Anthony's projects or modeled on one:

- **a small clear fix:** add one missing web server rule
- **changing existing code by house rules:** hide a cost line in visitor spaces of the BotCouncil window, as one block in the right place, touching nothing else
- **a new tool from a description:** a code finder that prints one named block of a 5,000-line server file, hiding keys and internet addresses
- **a new tool needing exactness:** a trimmer that moves old sections of a notes file into its archive without losing or changing a single character
- **fixing a bug from an error message:** a summary tool that crashes on old entries; the check also runs it on a file the worker never saw, so a fix that only works on the sample fails
- **reading code and reporting without changing anything:** six questions about the autobot's own program
- **one change across two files:** a word count button on a page plus the matching piece in its back end, adding lines only

Every check was tested before it was used: it fails on the starting files and on several wrong answers, and passes on a right one.

## 6. The tests we stopped running, and why

By the end of the measuring batch, most kinds of work were passed by every model in both modes. Repeating those jobs tells you nothing new: when everything passes, the jobs are too easy to tell the models apart. So they stopped being run as a measure. What still matters for those kinds is cost, and the model picker (section 8) already uses it.

Two things kept their value: the cheap models' cost per pass, and the kinds where the weaker cheap model still slipped without the tools. New measuring needs harder jobs, the kind where cheap models really struggle, so the picker has something to decide on.

## 7. Browser autobots

A browser autobot does a job on websites. It runs a hidden Chromium browser inside its own fenced-off box, driven by browser-use (https://github.com/browser-use/browser-use), with the same DeepSeek models on DeepInfra. It reads pages as text: DeepSeek can't see images. browser-use's own reporting back to its makers (telemetry and cloud sync) is switched off, and the AI key goes into the box as a setting, never in a file.

It practises on a made-up job site that runs inside the box, so nothing real is touched. The applicant, Sam Rivera, is made up too. Everything the site receives is recorded, and a hidden check grades it, exactly like the coding jobs.

Four practice jobs, one for each thing real job applications need:

- **a one-page application:** a dropdown, checkboxes, a date, a choice, an exact note, a resume upload, then report the reference number
- **a three-step application with screening questions**, answered by working them out from the details ("takes the bus everywhere and has never been late" means yes to reliable transportation)
- **knowing when to stop:** a question the details can't answer (a forklift certificate). Passing means submitting nothing and naming the question, instead of guessing.
- **filling in without submitting:** the form saves a draft as you type; passing means the draft is right and Submit was never pressed. This is how real applications start: the autobot fills them in overnight, and a person checks and submits in the morning.

**Results, 20 October 2026:**

| | passed | money per passed job |
|---|---|---|
| V4.1 Flash, one-page application | 3 of 3 | about 3 cents |
| V4.1 Flash, three-step application | 3 of 3 | about 3 cents |
| V4.1 Flash, knowing when to stop | 3 of 3 | about 1.5 cents |
| V4.1 Flash, fill in without submitting | 3 of 3 | about 12 cents (it fills each field slowly while the form saves; about 5 minutes) |
| Flash, all four jobs | 0 of 12 | |
| Pro, all four jobs | 0 of 8 | |

(The costs come from the later runs: in the first run the word-piece count that the cost is worked out from wasn't being recorded yet.)

**Why the cheapest model was the only one that worked.** The browser tool needs every answer from the AI in one exact shape: which action, then its details ("click, on box 25"). Flash and Pro kept leaving out which action ("box 25"), so the tool gave up on them after three bad answers, usually within a minute, before they'd tried the job at all. Showing them worked examples of the right shape (a browser-use setting) didn't help: 0 of 8. V4.1 Flash sent the right shape every time.

So for driving a browser, what decided success wasn't how strong a model was: it was whether it speaks the browser tool's format. The model picker uses only ways that have proven themselves, so browser jobs go to V4.1 Flash.

**Next:** real job sites, in stages. First the autobot fills applications in overnight and stops before Submit; a person checks and submits in the morning. Later, for simple applications only, it submits on its own once that has proven itself. Logins for job sites stay in the person's own browser or a private file, never in a card or a public file.

## 8. Picking the AI for each job

`--models auto` reads the card's kind of work and the measured results, then tries the cheapest proven way first: the lowest money per passed job among the ways that passed at least 3 times in 4. If that fails, it moves to the next one, always ending with the strong model with the full tools. A kind with no results yet starts with V4.1 Flash with the tools, then Flash with the tools, then Pro with the tools.

Anthony's rule for it: "It can't end up costing more than just using the stronger AI in the first place." On these results, every proven cheap way costs less per pass than Pro does.

## 9. Running your own

You need a Linux computer with Docker, Python 3.12, and an AI key on DeepInfra (https://deepinfra.com) with a spending limit set on the key. Any host with an OpenAI-style chat address works with small changes.

1. Make a folder `~/BC_private/autobot/` and put the files from `code/` in it (the program expects that folder; change `BASE` at the top of `autobot.py` to use another).
2. Make a Python space there and install the worker: `python3 -m venv venv && venv/bin/pip install mini-swe-agent requests pyyaml`
3. Put your key on the first line of `deepinfra-key.txt` in that folder. It never goes in a card or a public file.
4. Build the two boxes: `docker build -t autobot-box box/` and `docker build -t autobot-browser box-browser/`
5. Try the tiny job: `venv/bin/python autobot.py run smoke-hello --models flash41`
6. Write your own cards in `tasks/NAME/card.txt` (copy one of the examples).

The cards in `code/tasks/` point at Anthony's own files (his web server settings, his server program); they're examples of the format, and won't run on your computer without your own files in their place. The browser practice cards run anywhere: they bring their own practice site.

Easiest route if you don't code: give this page and the `code/` folder to an AI that can run commands, and ask it to set this up with you step by step. That's how all of it was built.

## 10. What is in this project

- `code/autobot.py`: the coding autobot (jobs, tools, the hidden check, reports, the failure notebook, the model picker).
- `code/bbot.py`: the browser autobot.
- `code/abwatch.py`: a live view in the terminal: what each worker is doing right now, its steps and money.
- `code/analyze-bc8.py`: the results tables.
- `code/measure-bc8.sh`: the measuring batch.
- `code/house-rules.txt`: what every worker reads before every job.
- `code/box/`: the coding box (internet, toolbox, the web page reader).
- `code/box-browser/`: the browser box (Chromium, browser-use, the practice site's server).
- `code/tasks/`: every job card, with the practice job site and its made-up applicant.
- `data/results.jsonl`: every graded job: kind, model, mode, pass or fail, money, steps, minutes, and the check after each stage.
- `data/tables.txt`: the tables in this page and more.
- `data/browser-results.txt`: the browser jobs so far.
- `data/easier.txt`: what workers said would have made their jobs easier.

## 11. Credits

- Anthony Brasher (https://github.com/Queuevius): design, direction and testing.
- Built with Claude (Anthropic) and DeepSeek. The coding worker is mini-swe-agent (https://github.com/SWE-agent/mini-swe-agent); the browser worker is browser-use (https://github.com/browser-use/browser-use).

## 12. Quotes about this

Said by Anthony while building it, word for word, so readers can see what was said before an AI reworked it.

- "If I could setup my BC with autopilot mode, it could develop things for me and I could just check in from time to time to see if it completed its task effectively."
- "If I can automate what I'm doing I'll be able to accomplish WAY more per day."
- "I don't wanna use Chinese servers or companies. Deepseek is an open source model more respectable people can run. I wanna be able to tell my users that we use servers with data protection, that's a big deal, part of how we're seen."
- "Paying a few extra dollars is way better than losing a few hours trying to get something to work. By a longshot."
- "I just realized how dumb it is of me to say we should test 'easy medium hard', when this all really comes down to capabilities, various types of work and sophistication."
- "I bet if failures are documented our system can automatically refine how it works. Even if it needs me to do things via terminal. I like being in the loop anyhow."
- "As long as I can test it first that sounds great to me. I'd love to just press a button and it deploys approved changes. I'm just saying I need to approve stuff before it changes anything."
- "Let's give them all the tools. I am increasingly suspiscious of the 'advice' we got from people online. From now on we use logic to determine what will help AI."
- "We might be able to get a bit more efficiency if we enable the cheaper AI to try each task, and if it's a little bit off it tries again, but if the task is too complex it switches over to a stronger AI."
- "It can't end up costing more than just using the stronger AI in the first place."
- "Note that we still have no idea how capable weak AIs are gonna become. But the better we can make them the more likely I am to not only empower people via Needpedia, but also land other opportunities."
- "Having data changes my github post about autobots and other things from opinion to data science."
- "If we're done proving my setup works we should add a section on that and how we stopped even bothering with some tests because they became pointless. As long as the data backs everything up."
- "Then we can use the autobots to improve the autobots, optimize as much as is reasonably possible, then get to task on all loose ends and then create an automated system for identifying more work for the autobots, including UX design."
