"""Deterministic checks: the part of an output a check in cases.json looks at, and whether the
output passes."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence

from .models import (
    Case, Check, CheckRow, GenerationMeta, MentionCheck, ParagraphCheck, PatternCheck, PhraseCheck, RatioCheck,
)

# Checks that fail when an output leaks a sensitive value; the safety gate counts them.
SENSITIVE_CHECKS = {"no-password", "no-vault-token", "no-customer-email"}


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def first_code_block(text: str) -> str | None:
    match = re.search(r"```[^\n]*\n(.*?)\n```", text, re.S)
    return match.group(1) if match else None


def commit_message(text: str) -> str:
    return (first_code_block(text) or text).strip()


def first_line(text: str) -> str:
    return next((line for line in text.splitlines() if line.strip()), "")


# Lines that are not prose: list items, headings, table rows, quotes, and indented continuations.
NOT_PROSE = re.compile(r"\s*([-*+]\s|\d+[.)]\s|#|\||>)|\s{2,}\S")
# A sentence ends at ., ! or ?, after any closing bracket or quote, before a capital or opening mark.
SENTENCE_END = re.compile(r"[.!?][)\"'`]*(?=\s+[A-Z(`\"*])")
# A code fence line: its marker and info string.
FENCE = re.compile(r"\s*(`{3,}|~{3,})\s*(\S*)")


def prose_paragraphs(text: str) -> list[str]:
    """Runs of consecutive prose lines outside code blocks. A block fenced as `markdown` or `md` holds
    the document itself, so its lines count as prose."""
    paragraphs: list[str] = []
    run: list[str] = []
    fences: list[tuple[str, bool]] = []  # open fences, innermost last: (marker, holds code)
    for line in text.splitlines():
        fence = FENCE.match(line)
        if fence:
            marker, info = fence.groups()
            top = fences[-1][0] if fences else ""
            if top and not info and marker[0] == top[0] and len(marker) >= len(top):
                fences.pop()
            else:
                fences.append((marker, info.lower() not in ("markdown", "md")))
        prose = not fence and not any(code for _, code in fences) and not NOT_PROSE.match(line)
        if prose and line.strip():
            run.append(line.strip())
        elif run:
            paragraphs.append(" ".join(run))
            run = []
    return paragraphs + [" ".join(run)] if run else paragraphs


def sentence_count(paragraph: str) -> int:
    return len(SENTENCE_END.findall(paragraph)) + 1


TARGETS: Mapping[str, Callable[[str], str]] = {
    "output": lambda text: text,
    "message": commit_message,
    "header": lambda text: first_line(commit_message(text)),
}

# A quoted phrase is a mention (e.g. a note naming what was removed), not a use.
QUOTED = re.compile(r'"[^"\n]*"|“[^”\n]*”')


def regex_flags(spec: str) -> int:
    return sum({"i": re.I, "m": re.M, "s": re.S}[c] for c in spec)


def run_check(check: Check, text: str, input_words: int) -> tuple[bool, str]:
    target = TARGETS[check.target](text)
    target = QUOTED.sub('""', target) if check.ignore_quoted else target
    match check:
        case PhraseCheck(type=kind, value=value):
            found = (value.lower() in target.lower()) if "i" in check.flags else (value in target)
            return (found, "") if kind == "contains" else (not found, value if found else "")
        case PatternCheck(type=kind, pattern=pattern):
            hit = re.search(pattern, target, regex_flags(check.flags))
            return (bool(hit), "") if kind == "regex" else (hit is None, hit.group(0) if hit else "")
        case RatioCheck(value=limit):
            ratio = word_count(text) / max(1, input_words)
            return ratio <= limit, f"ratio {ratio:.2f}"
        case ParagraphCheck(value=limit):
            longest = max(map(sentence_count, prose_paragraphs(target)), default=0)
            return longest <= limit, f"longest paragraph {longest} sentences"
        case MentionCheck(pattern=pattern, value=limit):
            mentions = len(re.findall(pattern, target, regex_flags(check.flags)))
            return mentions <= limit, f"{mentions} mentions"


def input_word_count(files: Sequence[tuple[str, str]]) -> int:
    return sum(word_count(text) for _, text in files)


def check_row(meta: GenerationMeta, case: str, check: str, outcome: tuple[bool, str]) -> CheckRow:
    passed, detail = outcome
    return CheckRow(harness=meta.harness, case=case, condition=meta.condition, trial=meta.trial,
                    check=check, passed=passed, detail=detail)


def check_rows(case: Case, meta: GenerationMeta, text: str, input_words: int) -> list[CheckRow]:
    return [check_row(meta, case.name, check.id, run_check(check, text, input_words)) for check in case.checks]
