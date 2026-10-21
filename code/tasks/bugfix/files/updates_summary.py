#!/usr/bin/env python3
"""updates_summary.py FILE - one line per tag: how many entries carry it, and the newest date.
Entries in an updates post are separated by a line holding only ---.
Each entry starts with a tags line, then a From line with a date like "29 September 2026"."""
import re
import sys
from datetime import date

MONTHS = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July",
                                      "August", "September", "October", "November", "December"], 1)}


def parse(text):
    entries = []
    for block in text.split("\n---\n"):
        lines = [l for l in block.strip().splitlines() if l.strip()]
        if not lines:
            continue
        tags = [t.strip() for t in lines[0].split(":", 1)[1].split(",") if t.strip()]
        m = re.search(r"(\d{1,2}) ([A-Z][a-z]+) (\d{4})", lines[1])
        d = date(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1)))
        entries.append({"tags": tags, "date": d})
    return entries


def summary(entries):
    by_tag = {}
    for e in entries:
        for t in e["tags"]:
            n, newest = by_tag.get(t, (0, e["date"]))
            by_tag[t] = (n + 1, max(newest, e["date"]))
    return [f"{t}: {n} entries, newest {d.isoformat()}" for t, (n, d) in sorted(by_tag.items())]


if __name__ == "__main__":
    print("\n".join(summary(parse(open(sys.argv[1]).read()))))
