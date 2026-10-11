"""The calibration packet: which judged groups a person grades blind, and the packet and answer
key that show them."""

from __future__ import annotations

import random
import re
from collections.abc import Mapping, Sequence

from .models import Case, StoredJudgement
from .report import group_verdict

VERDICT_MARKS: Mapping[int | None, str] = {1: "X", -1: "Y", 0: "tie", None: "–"}
CONDITION_NAMES = {"B": "short rule (B)", "C": "skill (C)"}
Pick = tuple[Case, int, str, str]  # case, trial, condition shown as X, condition shown as Y


def fenced(text: str, info: str = "") -> str:
    """Fence text with more backticks than it contains, so nested fences survive."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{info}\n{text.rstrip()}\n{fence}"


def calibration_picks(run_id: str, harness: str, cases: Sequence[Case], trials: int, groups: int,
                      complete: set[tuple[str, int]]) -> list[Pick]:
    """One random complete group per case, up to `groups`, with B and C as X and Y in random order."""
    rng = random.Random(f"{run_id}:{harness}:calibrate")
    order = list(cases)
    rng.shuffle(order)
    picks: list[Pick] = []
    for case in order:
        trial_order = list(range(1, trials + 1))
        rng.shuffle(trial_order)
        trial = next((t for t in trial_order if (case.name, t) in complete), None)
        if trial is None:
            continue
        x, y = rng.sample(["B", "C"], 2)
        picks.append((case, trial, x, y))
        if len(picks) == groups:
            break
    return picks


def packet_header(run_id: str, harness: str) -> list[str]:
    return [f"# Calibration packet: {run_id}, {harness} writer", "",
            "For each group, read the task and the source, then decide which response serves the "
            "intended reader better, or call it a tie, and note why. Open `key.md` only when you "
            "have decided every group.", ""]


def key_header(run_id: str, harness: str) -> list[str]:
    return [f"# Calibration key: {run_id}, {harness} writer", "",
            "Judge columns give each judge's group verdict: the response that ranked better on more "
            "of the four dimensions. Fill in your call, then count agreements per judge.", "",
            "| Group | Case | Trial | X | Y | Claude judge | Codex judge | Your call |",
            "|---|---|---|---|---|---|---|---|"]


def calibration_group(number: int, pick: Pick, files: Sequence[tuple[str, str]], outputs: Mapping[str, str],
                      judgements: Sequence[StoredJudgement]) -> tuple[list[str], str]:
    """A group's packet section and its row in the key."""
    case, trial, x, y = pick
    verdicts = [VERDICT_MARKS[group_verdict(j, x, y)] for j in judgements]
    row = (f"| {number} | {case.name} | t{trial} | {CONDITION_NAMES[x]} | {CONDITION_NAMES[y]} | "
           f"{verdicts[0]} | {verdicts[1]} | |")
    section = [f"## Group {number}", "", "### Task", "", case.prompt.strip(), "",
               *[line for name, text in files for line in (f"### Source: `{name}`", "", fenced(text, "markdown"), "")],
               *[line for label, cond in (("X", x), ("Y", y))
                 for line in (f"### Response {label}", "", fenced(outputs[cond], "markdown"), "")],
               "**Your call** (X, Y, or tie) and why:", "", "---", ""]
    return section, row
