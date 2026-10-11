"""Lint rules for SKILL.md, from the Agent Skills spec and this repository's budgets, and for the
eval data."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence

from .checks import regex_flags
from .models import SKILL_NAME, Case, Check, PatternCheck, TriggerQuery
from .prompts import split_frontmatter

DESCRIPTION_BUDGET_CHARS = 300  # own budget; the spec allows 1024
BODY_BUDGET_TOKENS = 2500  # estimated as characters / 4

SKILL_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}


def scalar(meta: Mapping[str, str | dict[str, str]], key: str) -> str:
    value = meta.get(key, "")
    return value if isinstance(value, str) else ""


def relative_links(body: str) -> list[str]:
    return [t for t in re.findall(r"\]\(([^)#\s]+)\)", body) if not re.match(r"[a-z]+://", t)]


def plain_yaml_problem(line: str) -> str | None:
    # Harnesses with strict YAML parsers (Pi, Codex's validator) silently drop a skill whose
    # frontmatter does not parse; an unquoted ": " or " #" inside a value is the usual cause.
    value = line.partition(":")[2].strip()
    if value and value[0] not in "\"'" and (": " in value or " #" in value or value[0] in "*&!|>%@`{["):
        return f"frontmatter value is not valid plain YAML; quote it: {line.strip()[:60]}"
    return None


def lint_skill(text: str, broken_links: Sequence[str], openai_yaml: str | None) -> tuple[list[str], list[str], str]:
    """Errors, warnings, and a size summary for SKILL.md."""
    meta, body = split_frontmatter(text)
    name, desc = scalar(meta, "name"), scalar(meta, "description")
    block = re.match(r"\A---\n(.*?)\n---", text, re.S)
    unknown = sorted(set(meta) - SKILL_KEYS)
    lines, tokens = body.count("\n") + 1, len(body) // 4
    short = re.search(r'short_description:\s*"([^"]*)"', openai_yaml) if openai_yaml is not None else None
    rules = [
        (not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name) or len(name) > 64,
         f"name {name!r} violates the Agent Skills name rules"),
        (name != SKILL_NAME, f"name {name!r} does not match directory {SKILL_NAME!r}"),
        (not 1 <= len(desc) <= 1024, f"description length {len(desc)} is outside 1–1024"),
        (1 <= len(desc) <= 1024 and len(desc) > DESCRIPTION_BUDGET_CHARS,
         f"description is {len(desc)} chars; budget is {DESCRIPTION_BUDGET_CHARS}"),
        (bool(re.search(r"[<>]", desc)), "description must not contain XML tags"),
        (bool(unknown), f"frontmatter keys outside the Agent Skills spec: {unknown}"),
        (lines > 500, f"body has {lines} lines; the spec recommends under 500"),
        (tokens > BODY_BUDGET_TOKENS, f"body is ~{tokens} tokens; budget is {BODY_BUDGET_TOKENS}"),
        *[(True, f"broken relative link: {target}") for target in broken_links],
        (short is not None and not 25 <= len(short.group(1)) <= 64,
         f"openai.yaml short_description is {len(short.group(1)) if short else 0} chars; Codex wants 25–64"),
    ]
    yaml_problems = [p for p in map(plain_yaml_problem, block.group(1).splitlines() if block else []) if p]
    errors = yaml_problems + [message for failed, message in rules if failed]
    warnings = ["description should be third person"] if re.match(r"(I|You|We)\b", desc) else []
    summary = f"skill: description {len(desc)} chars (~{len(desc) // 4} tokens); body {lines} lines (~{tokens} tokens)"
    return errors, warnings, summary


def pattern_problem(where: str, check: Check) -> str | None:
    if not isinstance(check, PatternCheck):
        return None
    try:
        re.compile(check.pattern, regex_flags(check.flags))
    except re.error as exc:
        return f"{where}: {check.id} pattern does not compile: {exc}"
    return None


def case_problems(case: Case, earlier: Sequence[Case], missing_files: Iterable[str]) -> list[str]:
    where = f"case {case.name}"
    check_ids = [c.id for c in case.checks]
    rules = [
        (any(c.id == case.id or c.name == case.name for c in earlier), f"{where}: duplicate id or name"),
        *[(f in missing_files, f"{where}: missing file {f}") for f in case.files],
        (len(set(check_ids)) != len(check_ids), f"{where}: duplicate check ids"),
    ]
    return [message for failed, message in rules if failed] + \
        [p for p in (pattern_problem(where, c) for c in case.checks) if p]


def lint_data(cases: Sequence[Case], missing_files: Iterable[str],
              triggers: Sequence[TriggerQuery]) -> tuple[list[str], list[str], str]:
    """Errors, warnings, and a size summary for cases.json and triggers.json."""
    errors = [p for i, case in enumerate(cases) for p in case_problems(case, cases[:i], missing_files)]
    positives = sum(1 for t in triggers if t.should_trigger)
    negatives = len(triggers) - positives
    warnings = [f"triggers.json is unbalanced: {positives} positive, {negatives} negative"] \
        if abs(positives - negatives) > 2 else []
    summary = (f"data: {len(cases)} cases, {sum(len(c.checks) for c in cases)} checks, "
               f"{sum(len(c.expectations) for c in cases)} expectations, {len(triggers)} trigger queries")
    return errors, warnings, summary
