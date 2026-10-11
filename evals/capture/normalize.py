"""normalize.py OUT WORK: rewrite every file under OUT so it no longer depends on where or when it ran.

Replaces the work directory, temporary directory names, ISO timestamps, durations, and default
(timestamp) run ids.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

out, work = Path(sys.argv[1]), sys.argv[2]
works = {work, os.path.realpath(work), work.removeprefix("/private")}
SUBS = [
    (re.compile(r"/[^\s\"'`]*?(tw-(?:eval|trigger)-)[A-Za-z0-9_]+"), r"<TMP>/\1X"),
    (re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?\+00:00"), "<TS>"),
    (re.compile(r'"duration_s": [0-9.]+'), '"duration_s": <D>'),
    (re.compile(r"\b20\d{6}-\d{6}\b"), "<RUNID>"),
]
PROGRESS = re.compile(r"(?m)(?<=\S) \d+s$")  # generate's "... ok 12s"

for path in sorted(p for p in out.rglob("*") if p.is_file()):
    text = path.read_text(encoding="utf-8")
    new = text
    for w in sorted(works, key=len, reverse=True):
        new = new.replace(w, "<WORK>")
    for pattern, repl in SUBS:
        new = pattern.sub(repl, new)
    if path.suffix == ".out":
        new = PROGRESS.sub(" <D>s", new)
    if new != text:
        path.write_text(new, encoding="utf-8")
