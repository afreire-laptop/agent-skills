"""snap.py ROOT STATE [STEP_DIR]: record what changed under ROOT since the last snapshot.

Writes STEP_DIR/files.txt (A added, M modified, W rewritten unchanged, D deleted; directories
end in /) and copies added and modified
files to STEP_DIR/written/. __pycache__ directories are ignored. Updates STATE. Content is compared
with durations and timestamps blanked, as normalize.py does, so timing cannot turn W into M.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

root, state = Path(sys.argv[1]), Path(sys.argv[2])
step = Path(sys.argv[3]) if len(sys.argv) > 3 else None


TIMING = re.compile(rb'"duration_s": [0-9.]+|\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?\+00:00')


def tree() -> dict[str, str]:
    entries: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for d in dirnames:
            entries[Path(dirpath, d).relative_to(root).as_posix() + "/"] = "dir"
        for f in filenames:
            path = Path(dirpath, f)
            rel = path.relative_to(root).as_posix()
            entries[rel] = ("link:" + os.readlink(path)) if path.is_symlink() \
                else f"{hashlib.sha256(TIMING.sub(b"", path.read_bytes())).hexdigest()} {path.stat().st_mtime_ns}"
    return entries


now = tree()
if step is not None and state.exists():
    before = json.loads(state.read_text())
    changes = sorted([("A", p) for p in now if p not in before] + [("D", p) for p in before if p not in now]
                     + [("M" if now[p].split()[0] != before[p].split()[0] else "W", p)
                        for p in now if p in before and now[p] != before[p]], key=lambda c: c[1])
    step.mkdir(parents=True, exist_ok=True)
    (step / "files.txt").write_text("".join(f"{kind} {path}\n" for kind, path in changes))
    for kind, path in changes:
        if kind != "D" and not path.endswith("/"):
            dest = step / "written" / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / path, dest)
state.write_text(json.dumps(now))
