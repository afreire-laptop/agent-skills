"""What the runner sends to the CLIs: the writer prompt, the judge prompt and schema, and the
check that a judge's reply is complete."""

from __future__ import annotations

import hashlib
import random
import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence

from pydantic import ValidationError

from .models import DIMENSIONS, Case, Guidance, JudgeResult, first_error

NO_REPO_NOTE = "Everything you need is included below; there is no repository checkout to inspect."


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def split_frontmatter(text: str) -> tuple[dict[str, str | dict[str, str]], str]:
    """Parse the simple YAML frontmatter used by SKILL.md (scalars and one-level maps)."""
    match = re.match(r"\A---\n(.*?)\n---\n?(.*)\Z", text, re.S)
    if not match:
        return {}, text
    meta: dict[str, str | dict[str, str]] = {}
    parent: str | None = None
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, _, value = line.strip().partition(":")
        value = value.strip().strip('"').strip("'")
        nested = meta.get(parent) if parent else None
        if line.startswith((" ", "\t")) and isinstance(nested, dict):
            nested[key] = value
        elif value:
            meta[key], parent = value, None
        else:
            meta[key], parent = {}, key
    return meta, match.group(2)


GUIDANCE: Mapping[str, Callable[[Guidance], str | None]] = {
    "A": lambda g: None,
    "B": lambda g: g.short_rule,
    "C": lambda g: g.skill_body,
}


def file_blocks(files: Sequence[tuple[str, str]]) -> list[str]:
    return [f'<file name="{name}">\n{text.rstrip()}\n</file>' for name, text in files]


def build_prompt(case: Case, guidance: str | None, files: Sequence[tuple[str, str]]) -> str:
    preamble = [
        "Apply the following writing guidance to this task.\n\n"
        f"<writing-guidance>\n{guidance}\n</writing-guidance>"
    ] if guidance else []
    return "\n\n".join([*preamble, f"{case.prompt}\n\n{NO_REPO_NOTE}", *file_blocks(files)]) + "\n"


def judge_schema() -> dict[str, object]:
    def obj(props: dict[str, object]) -> dict[str, object]:
        return {"type": "object", "additionalProperties": False,
                "required": list(props), "properties": props}

    string, verdict = {"type": "string"}, {"type": "string", "enum": ["pass", "fail", "unknown"]}
    response = obj({
        "label": string,
        "analysis": string,
        "expectations": {"type": "array", "items": obj(
            {"index": {"type": "integer"}, "verdict": verdict, "evidence": string})},
        "invented_facts": {"type": "array", "items": string},
        "omitted_facts": {"type": "array", "items": string},
        "fidelity": {"type": "string", "enum": ["pass", "fail"]},
    })
    dimension = obj({
        "dimension": {"type": "string", "enum": list(DIMENSIONS)},
        "analysis": string,
        "ranks": {"type": "array", "items": obj({"label": string, "rank": {"type": "integer"}})},
    })
    return obj({"responses": {"type": "array", "items": response},
                "dimensions": {"type": "array", "items": dimension}})


def build_judge_prompt(template: str, case: Case, files: Sequence[tuple[str, str]],
                       responses: Mapping[str, str]) -> str:
    expectations = "\n".join(f"{i}. {e}" for i, e in enumerate(case.expectations, 1))
    blocks = "\n\n".join(f'<response label="{label}">\n{text.rstrip()}\n</response>'
                         for label, text in responses.items())
    return (template
            .replace("{{TASK}}", case.prompt)
            .replace("{{FILES}}", "\n\n".join(file_blocks(files)))
            .replace("{{EXPECTATIONS}}", expectations)
            .replace("{{RESPONSES}}", blocks))


def judgement_problems(result: JudgeResult, labels: Sequence[str], n_expectations: int) -> Iterator[str]:
    if sorted(r.label for r in result.responses) != sorted(labels):
        yield "response labels do not match"
    for response in result.responses:
        if sorted(e.index for e in response.expectations) != list(range(1, n_expectations + 1)):
            yield f"expectation indexes incomplete for {response.label}"
    if sorted(d.dimension for d in result.dimensions) != sorted(DIMENSIONS):
        yield "dimensions incomplete"
    for dim in result.dimensions:
        if sorted(r.label for r in dim.ranks) != sorted(labels):
            yield f"ranks incomplete for {dim.dimension}"


def judgement_error(structured: object, labels: Sequence[str], n_expectations: int) -> str | None:
    """Why a judge's structured output is unusable, or None if it is complete."""
    if not isinstance(structured, dict):
        return "no structured output"
    try:
        result = JudgeResult.model_validate(structured)
    except ValidationError as exc:
        return f"output does not match the schema: {first_error(exc)}"
    return next(judgement_problems(result, labels, n_expectations), None)


def blind_mapping(seed: str, conditions: Iterable[str]) -> dict[str, str]:
    """Anonymous labels R1, R2, ... for the conditions, in an order seeded per judge."""
    order = sorted(conditions)
    random.Random(seed).shuffle(order)
    return {f"R{i}": cond for i, cond in enumerate(order, 1)}
