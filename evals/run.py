#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pydantic==2.13.5"]
# ///
"""Evaluation runner for the technical-writing skill.

Run with `uv run evals/run.py` (Python 3.10+); Pydantic is the one dependency.
Drives the `claude` and `codex` CLIs with whatever login they already have, so
no API keys are needed.

Subcommands:
  lint      validate the skill's frontmatter and the eval data files
  selftest  check the graders: references pass every check, raw inputs fail some
  generate  run each case under conditions A (no guidance), B (short rule),
            C (skill) for N trials per harness
  check     apply the deterministic checks in cases.json to every output
  judge     blind LLM grading of each (case, trial) group by a two-family panel
  triggers  measure whether the skill's description triggers on the queries
            in triggers.json
  report    aggregate everything into report.md / report.json with gates
  all       generate, check, judge, triggers, report
  calibrate write a blind packet of judged groups for the human judge check

Runs are resumable: finished units are skipped, failed ones are retried.
See evals/README.md for the protocol and the meaning of each metric.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import random
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from pathlib import Path
from typing import Annotated, Literal, TypeVar, get_args

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

ROOT = Path(__file__).resolve().parent.parent
EVALS = ROOT / "evals"
SKILL_NAME = "technical-writing"
SKILL_DIR = ROOT / "skills" / SKILL_NAME
SKILL_MD = SKILL_DIR / "SKILL.md"
CASES_FILE = EVALS / "cases.json"
TRIGGERS_FILE = EVALS / "triggers.json"
SHORT_RULE_FILE = EVALS / "conditions" / "short-rule.md"
JUDGE_TEMPLATE = EVALS / "judge.md"
RUNS_DIR = EVALS / "runs"

CONDITIONS = {"A": "no guidance", "B": "short rule", "C": "skill"}
HARNESSES = ("claude", "codex")
Dimension = Literal["clarity", "naturalness", "brevity", "conventions"]
DIMENSIONS: tuple[Dimension, ...] = get_args(Dimension)
SENSITIVE_CHECKS = {"no-password", "no-vault-token", "no-customer-email"}

# Pre-registered thresholds (see evals/README.md, "Release gates").
DESCRIPTION_BUDGET_CHARS = 300  # own budget; the spec allows 1024
BODY_BUDGET_TOKENS = 2500  # estimated as characters / 4
FIDELITY_TOLERANCE = 0.02  # C may trail B by at most this on pass rates
MIN_TRIGGER_RECALL = 0.8
MAX_TRIGGER_FALSE_POSITIVE = 0.2

NO_REPO_NOTE = "Everything you need is included below; there is no repository checkout to inspect."


# -------------------------------------------------------------------------- models


class Model(BaseModel):
    """Immutable, strictly typed data. Unknown keys are errors."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class External(Model):
    """Data that a CLI or a model wrote. Unknown keys are ignored."""

    model_config = ConfigDict(frozen=True, strict=True, extra="ignore")


M = TypeVar("M", bound=BaseModel)


class CheckBase(Model):
    id: str
    target: Literal["output", "message", "header"] = "output"
    flags: Annotated[str, Field(pattern=r"^[ims]*$")] = ""
    ignore_quoted: bool = False


class PhraseCheck(CheckBase):
    type: Literal["contains", "not_contains"]
    value: str


class PatternCheck(CheckBase):
    type: Literal["regex", "not_regex"]
    pattern: str


class RatioCheck(CheckBase):
    type: Literal["max_word_ratio"]
    value: float


Check = Annotated[PhraseCheck | PatternCheck | RatioCheck, Field(discriminator="type")]


class Case(Model):
    id: int
    name: str
    artifact: str | None = None
    prompt: str
    files: Sequence[str]
    expected_output: str | None = None
    expectations: Sequence[str]
    checks: Sequence[Check]


class CaseFile(Model):
    skill_name: str | None = None
    evals: Sequence[Case]


class TriggerQuery(Model):
    query: str
    should_trigger: bool
    note: str | None = None


TRIGGER_QUERIES = TypeAdapter(tuple[TriggerQuery, ...])


class Guidance(Model):
    """The writing guidance conditions B and C add to the task."""

    short_rule: str
    skill_body: str | None


class HarnessOptions(Model):
    claude_model: str | None = None
    claude_effort: str | None = None
    codex_model: str | None = None
    codex_effort: str | None = None
    timeout: int = 600

    def with_effort(self, effort: str | None) -> HarnessOptions:
        return self.model_copy(update={"claude_effort": effort, "codex_effort": effort}) if effort else self


Usage = Mapping[str, int | float | None]


class CallResult(Model):
    ok: bool
    text: str = ""
    structured: object = None
    usage: Usage = Field(default_factory=dict)
    model: str | None = None
    duration_s: float = 0.0
    error: str | None = None


class ClaudeUsage(External):
    input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    output_tokens: int | None = None


class ClaudeReply(External):
    """`claude -p --output-format json`."""

    is_error: bool | None = None
    result: str | None = None
    structured_output: object = None
    usage: ClaudeUsage | None = None
    model_usage: Mapping[str, object] | None = Field(default=None, alias="modelUsage")
    total_cost_usd: int | float | None = None


class CodexUsage(External):
    input_tokens: int | None = None
    output_tokens: int | None = None


class CodexEvent(External):
    """One line of `codex exec --json`."""

    type: str | None = None
    usage: CodexUsage | None = None
    item: Mapping[str, object] | None = None


class ContentBlock(External):
    type: str | None = None
    name: str | None = None
    input: object = Field(default_factory=dict)


class ClaudeMessage(External):
    content: Sequence[ContentBlock] = ()


class ClaudeEvent(External):
    """One line of `claude -p --output-format stream-json`."""

    type: str | None = None
    subtype: str | None = None
    skills: Sequence[object] | None = None
    message: ClaudeMessage | None = None


class ExpectationGrade(External):
    index: int
    verdict: Literal["pass", "fail", "unknown"]
    evidence: str


class ResponseGrade(External):
    label: str
    analysis: str
    expectations: Sequence[ExpectationGrade]
    invented_facts: Sequence[str]
    omitted_facts: Sequence[str]
    fidelity: Literal["pass", "fail"]


class Rank(External):
    label: str
    rank: int


class DimensionRanks(External):
    dimension: Dimension
    analysis: str
    ranks: Sequence[Rank]


class JudgeResult(External):
    """A judge's structured output. Mirrors judge_schema(); selftest checks that they agree."""

    responses: Sequence[ResponseGrade]
    dimensions: Sequence[DimensionRanks]


class TriggerOutcome(Model):
    triggered: bool
    ended: str
    discovered: bool | None


class GenerationMeta(External):
    """gen/<harness>/<case>/<condition>/t<n>/meta.json."""

    ok: bool
    harness: str
    case: str
    condition: str
    trial: int
    prompt_sha: str | None = None
    model: str | None = None
    usage: Usage = Field(default_factory=dict)
    duration_s: float | None = None
    output_words: int
    error: str | None = None


class CheckRow(External):
    """One entry of checks.json."""

    harness: str
    case: str
    condition: str
    trial: int
    check: str
    passed: bool
    detail: str


CHECK_ROWS = TypeAdapter(tuple[CheckRow, ...])


class JudgedConditions(External):
    """The part of a stored judgement that says which conditions it compared."""

    mapping: Mapping[str, str]


class StoredJudgement(JudgedConditions):
    """judge/<harness>/<case>/t<n>/<judge>.json."""

    judge: str
    judge_model: str | None = None
    usage: Usage = Field(default_factory=dict)
    result: JudgeResult


class Judged(Model):
    """A stored judgement and the group its path names."""

    harness: str
    case: str
    trial: str
    judge: str  # from the file name
    judgement: StoredJudgement


class TriggerRun(External):
    """triggers/<harness>/q<nn>-r<n>.json."""

    triggered: bool
    ended: str
    discovered: bool | None
    harness: str
    index: int
    attempt: int
    query: str
    should_trigger: bool
    duration_s: float | None = None


class Invocation(Model):
    at: str
    argv: Sequence[str]
    git_commit: str | None
    git_dirty: bool
    skill_sha: str | None
    short_rule_sha: str
    cases_sha: str
    claude_version: str | None
    codex_version: str | None
    options: Mapping[str, object]


class Manifest(External):
    """manifest.json. Earlier invocations are kept as they were written."""

    created: str
    invocations: Sequence[Mapping[str, object]] = ()


# --------------------------------------------------------------------------- data


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def read_model(model: type[M], path: Path) -> M:
    return model.model_validate_json(read_text(path))


def parsed(model: type[M], raw: object) -> M | None:
    """`raw` as a `model`, or None if it does not validate."""
    try:
        return model.model_validate(raw)
    except ValidationError:
        return None


def first_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    return f"{'.'.join(map(str, error['loc']))}: {error['msg']}"


def json_values(lines: Iterable[str]) -> Iterator[object]:
    """The lines of a JSONL stream that parse as JSON; other lines are skipped."""
    for line in lines:
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def load_cases() -> Sequence[Case]:
    return CaseFile.model_validate_json(read_text(CASES_FILE)).evals


def load_triggers() -> tuple[TriggerQuery, ...]:
    return TRIGGER_QUERIES.validate_json(read_text(TRIGGERS_FILE))


def select_cases(cases: Sequence[Case], spec: str) -> tuple[list[Case], list[str]]:
    """The cases a comma-separated list of names or ids picks, and the entries that match none."""
    if spec == "all":
        return list(cases), []
    wanted = {s.strip() for s in spec.split(",")}
    chosen = [c for c in cases if c.name in wanted or str(c.id) in wanted]
    return chosen, sorted(wanted - {c.name for c in chosen} - {str(c.id) for c in chosen})


def cases_or_exit(spec: str) -> list[Case]:
    chosen, unknown = select_cases(load_cases(), spec)
    if unknown:
        sys.exit(f"unknown case(s): {', '.join(unknown)}")
    return chosen


def case_files(case: Case) -> list[tuple[str, str]]:
    return [(Path(f).name, read_text(ROOT / f)) for f in case.files]


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


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


def load_guidance() -> Guidance:
    body = split_frontmatter(read_text(SKILL_MD))[1].strip() if SKILL_MD.exists() else None
    return Guidance(short_rule=read_text(SHORT_RULE_FILE).strip(), skill_body=body)


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


# ------------------------------------------------------------------------ harnesses


def flag(name: str, value: str | None) -> list[str]:
    return [name, value] if value else []


def claude_command(opts: HarnessOptions, schema: Mapping[str, object] | None) -> list[str]:
    return ["claude", "-p", "--output-format", "json", "--tools", "",
            "--disable-slash-commands", "--no-session-persistence",
            *flag("--model", opts.claude_model), *flag("--effort", opts.claude_effort),
            *flag("--json-schema", json.dumps(schema) if schema else None)]


def parse_claude_reply(stdout: str, stderr: str, returncode: int, duration: float,
                       opts: HarnessOptions) -> CallResult:
    try:
        reply = ClaudeReply.model_validate_json(stdout)
    except ValidationError:
        return CallResult(ok=False, error=f"exit {returncode}: {(stderr or stdout)[-500:]}", duration_s=duration)
    usage = reply.usage or ClaudeUsage()
    return CallResult(
        ok=not reply.is_error and returncode == 0,
        text=reply.result or "",
        structured=reply.structured_output,
        usage={
            "input_tokens": sum(n or 0 for n in (usage.input_tokens, usage.cache_read_input_tokens,
                                                 usage.cache_creation_input_tokens)),
            "output_tokens": usage.output_tokens or 0,
            "cost_usd": reply.total_cost_usd,
        },
        model=",".join(sorted(reply.model_usage or {})) or opts.claude_model,
        duration_s=duration,
        error=None if not reply.is_error else str(reply.result)[:500],
    )


def codex_base_command(opts: HarnessOptions, cwd: str) -> list[str]:
    effort = f'model_reasoning_effort="{opts.codex_effort}"' if opts.codex_effort else None
    return ["codex", "exec", "--ignore-user-config", "--ephemeral", "-s", "read-only",
            "-C", cwd, "--json", *flag("-m", opts.codex_model), *flag("-c", effort)]


def codex_exec_command(opts: HarnessOptions, cwd: str, out_file: Path, schema_file: Path | None) -> list[str]:
    return [*codex_base_command(opts, cwd), "--skip-git-repo-check", "-o", str(out_file),
            *flag("--output-schema", str(schema_file) if schema_file else None), "-"]


def codex_usage(events: Sequence[CodexEvent]) -> dict[str, int]:
    completed = [e.usage for e in events if e.type == "turn.completed" and e.usage]
    return {"input_tokens": sum(u.input_tokens or 0 for u in completed),
            "output_tokens": sum(u.output_tokens or 0 for u in completed)}


def decode_json(text: str) -> tuple[object, str | None]:
    try:
        return json.loads(text), None
    except json.JSONDecodeError:
        return None, "final message is not valid JSON"


def parse_codex_output(stdout: str, stderr: str, returncode: int, text: str, want_json: bool,
                       duration: float, opts: HarnessOptions) -> CallResult:
    events = [(raw, event) for raw in json_values(stdout.splitlines())
              if (event := parsed(CodexEvent, raw)) is not None]
    failures = [json.dumps(raw)[:500] for raw, event in events if event.type in ("turn.failed", "error")]
    structured, invalid = decode_json(text) if want_json and text else (None, None)
    errors = failures + ([invalid] if invalid else [])
    ok = returncode == 0 and bool(text) and not errors
    return CallResult(ok=ok, text=text, structured=structured, usage=codex_usage([e for _, e in events]),
                      model=opts.codex_model, duration_s=duration,
                      error="; ".join(errors) or (None if ok else f"exit {returncode}: {stderr[-500:]}"))


def run_cli(cmd: list[str], prompt: str, cwd: str, timeout: int) -> subprocess.CompletedProcess[str] | None:
    """Run a CLI with the prompt on stdin; None if it timed out."""
    try:
        return subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=cwd, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


def call_claude(prompt: str, opts: HarnessOptions, schema: Mapping[str, object] | None = None) -> CallResult:
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="tw-eval-") as cwd:
        proc = run_cli(claude_command(opts, schema), prompt, cwd, opts.timeout)
        if proc is None:
            return CallResult(ok=False, error="timeout", duration_s=time.monotonic() - start)
    return parse_claude_reply(proc.stdout, proc.stderr, proc.returncode, time.monotonic() - start, opts)


def call_codex(prompt: str, opts: HarnessOptions, schema: Mapping[str, object] | None = None) -> CallResult:
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="tw-eval-") as cwd:
        out_file = Path(cwd) / ".last-message"
        schema_file = Path(cwd) / ".schema.json" if schema else None
        if schema_file:
            schema_file.write_text(json.dumps(schema))
        proc = run_cli(codex_exec_command(opts, cwd, out_file, schema_file), prompt, cwd, opts.timeout)
        if proc is None:
            return CallResult(ok=False, error="timeout", duration_s=time.monotonic() - start)
        text = read_text(out_file) if out_file.exists() else ""
    return parse_codex_output(proc.stdout, proc.stderr, proc.returncode, text, bool(schema),
                              time.monotonic() - start, opts)


def parallel(tasks: Sequence[Callable[[], str]], jobs: int) -> None:
    total = len(tasks)
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = [pool.submit(task) for task in tasks]
        for done, future in enumerate(as_completed(futures), 1):
            print(f"[{done}/{total}] {future.result()}", flush=True)


# ------------------------------------------------------------------------ generate


def gen_dir(run: Path, harness: str, case: str, condition: str, trial: int) -> Path:
    return run / "gen" / harness / case / condition / f"t{trial}"


def generation_ok(out: Path) -> bool:
    meta = out / "meta.json"
    return meta.exists() and read_model(GenerationMeta, meta).ok


def generation_meta(harness: str, case: str, condition: str, trial: int, prompt: str,
                    result: CallResult) -> GenerationMeta:
    return GenerationMeta(ok=result.ok, harness=harness, case=case, condition=condition, trial=trial,
                          prompt_sha=sha256(prompt), model=result.model, usage=result.usage,
                          duration_s=round(result.duration_s, 1), output_words=word_count(result.text),
                          error=result.error)


def generate_one(harness: str, case: str, condition: str, trial: int, prompt: str, out: Path,
                 opts: HarnessOptions) -> str:
    result = call(harness, prompt, opts)
    out.mkdir(parents=True, exist_ok=True)
    (out / "output.md").write_text(result.text, encoding="utf-8")
    meta = generation_meta(harness, case, condition, trial, prompt, result)
    (out / "meta.json").write_text(json.dumps(meta.model_dump(), indent=2), encoding="utf-8")
    status = "ok" if result.ok else f"FAILED ({result.error})"
    return f"{harness} {case} {condition} t{trial} {status} {result.duration_s:.0f}s"


def cmd_generate(args: argparse.Namespace, opts: HarnessOptions) -> None:
    run = run_dir(args.run_id)
    write_manifest(run, args, opts)
    cases = cases_or_exit(args.cases)
    guidance = load_guidance()
    if "C" in args.conditions and guidance.skill_body is None:
        sys.exit(f"condition C needs {SKILL_MD.relative_to(ROOT)}")
    files = {case.name: case_files(case) for case in cases}
    tasks = []
    for harness in args.harness:
        for case in cases:
            for condition in args.conditions:
                prompt = build_prompt(case, GUIDANCE[condition](guidance), files[case.name])
                for trial in range(1, args.trials + 1):
                    out = gen_dir(run, harness, case.name, condition, trial)
                    if generation_ok(out):
                        continue
                    tasks.append(partial(generate_one, harness, case.name, condition, trial, prompt, out, opts))
    print(f"generate: {len(tasks)} unit(s) to run in {run.relative_to(ROOT)}")
    parallel(tasks, args.jobs)


# --------------------------------------------------------------------------- check


def first_code_block(text: str) -> str | None:
    match = re.search(r"```[^\n]*\n(.*?)\n```", text, re.S)
    return match.group(1) if match else None


def commit_message(text: str) -> str:
    return (first_code_block(text) or text).strip()


def first_line(text: str) -> str:
    return next((line for line in text.splitlines() if line.strip()), "")


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


def input_word_count(files: Sequence[tuple[str, str]]) -> int:
    return sum(word_count(text) for _, text in files)


def check_row(meta: GenerationMeta, case: str, check: str, outcome: tuple[bool, str]) -> CheckRow:
    passed, detail = outcome
    return CheckRow(harness=meta.harness, case=case, condition=meta.condition, trial=meta.trial,
                    check=check, passed=passed, detail=detail)


def check_rows(case: Case, meta: GenerationMeta, text: str, input_words: int) -> list[CheckRow]:
    return [check_row(meta, case.name, check.id, run_check(check, text, input_words)) for check in case.checks]


def cmd_check(args: argparse.Namespace) -> None:
    run = run_dir(args.run_id, must_exist=True)
    rows: list[CheckRow] = []
    for case in load_cases():
        input_words = input_word_count(case_files(case))
        for meta_file in sorted((run / "gen").glob(f"*/{case.name}/*/t*/meta.json")):
            meta = read_model(GenerationMeta, meta_file)
            if meta.ok:
                rows += check_rows(case, meta, read_text(meta_file.parent / "output.md"), input_words)
    (run / "checks.json").write_text(json.dumps([r.model_dump() for r in rows], indent=1), encoding="utf-8")
    failed = sum(not r.passed for r in rows)
    print(f"check: {len(rows)} results, {failed} failed -> {(run / 'checks.json').relative_to(ROOT)}")


def reference_line(case: Case, reference: str, raw: str, input_words: int) -> tuple[bool, str]:
    """Whether the reference passes every check and the raw input fails at least one."""
    ref_failures = [c.id for c in case.checks if not run_check(c, reference, input_words)[0]]
    raw_failures = [c.id for c in case.checks if not run_check(c, raw, input_words)[0]]
    ok = not ref_failures and bool(raw_failures)
    return ok, (f"{'ok  ' if ok else 'FAIL'} {case.name}: reference fails {ref_failures or 'none'}; "
                f"raw input fails {len(raw_failures)}/{len(case.checks)} checks")


def cmd_selftest(_args: argparse.Namespace) -> int:
    """Grader self-test: each reference solution passes every check, and each raw
    fixture fails at least one, so the checks are satisfiable and discriminating."""
    errors = 0
    for case in load_cases():
        files = case_files(case)
        reference = EVALS / "reference" / f"{case.name}.md"
        if not reference.exists():
            print(f"FAIL {case.name}: no reference solution at {reference.relative_to(ROOT)}")
            errors += 1
            continue
        ok, line = reference_line(case, read_text(reference), "\n\n".join(text for _, text in files),
                                  input_word_count(files))
        print(line)
        errors += not ok
    errors += selftest_gates()
    errors += selftest_partial_judgement()
    errors += selftest_judge_schema()
    print("selftest: ok" if not errors else f"selftest: {errors} failure(s)")
    return 1 if errors else 0


def selftest_gates() -> int:
    """The release gates must fail closed on partial runs and decide correctly on full ones."""
    def harness(c_preferred: int, b_preferred: int, cases_preferred: int) -> HarnessStats:
        cond = ConditionStats(check_pass_rate=0.97, check_pass_sd=None, expectation_pass_rate=None,
                              expectation_unknown=0, fidelity_pass_rate=0.9, sensitive_leaks=0,
                              mean_input_tokens=None, mean_output_words=None)
        pair = PairStats(groups=GroupCounts(first=c_preferred, tie=0, second=b_preferred),
                         cases_preferred=tuple(f"case{i}" for i in range(cases_preferred)),
                         net_by_case={}, mean_case_preference=0.0, ci95=(0.0, 0.0))
        return HarnessStats(conditions={"B": cond, "C": cond}, pairs={"C_vs_B": pair},
                            triggers=TriggerStats(recall=0.9, false_positive_rate=0.1, runs=0, undiscovered_runs=0))

    full = {"claude": harness(30, 10, 5), "codex": harness(25, 15, 4)}
    scenarios: list[tuple[str, dict[str, HarnessStats], list[str], str]] = [
        ("empty run", {}, [], "INCOMPLETE"),
        ("one harness missing", {"claude": full["claude"]}, [], "INCOMPLETE"),
        ("no judge results", {**full, "codex": full["codex"].model_copy(update={"pairs": {}})}, [], "INCOMPLETE"),
        ("missing generations", full, ["generation codex/x/C/t3"], "INCOMPLETE"),
        ("complete and passing", full, [], "SHIP"),
        ("short rule as good", {**full, "codex": harness(10, 12, 2)}, [], "SIMPLIFY"),
    ]
    failures = 0
    for name, stats, missing, expected in scenarios:
        _, _, verdict = gates(stats, 7, HARNESSES, missing)
        ok = verdict.startswith(expected)
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} gates, {name}: {verdict}")
    return failures


def selftest_partial_judgement() -> int:
    """A group judged while one output was missing must count as missing until it is redone."""
    case = load_cases()[0].name
    params = ReportParams(run_id="selftest", harnesses=("claude",), conditions=("A", "B", "C"), trials=1,
                          judge_harness="claude", trigger_runs=0)
    complete = {("claude", case, c, 1) for c in params.conditions}
    failures = 0
    for mapping, expect_missing in (({"R1": "B", "R2": "C"}, True),
                                    ({"R1": "B", "R2": "C", "R3": "A"}, False)):
        judged = {("claude", case, "t1", "claude"): set(mapping.values())}
        missing = missing_results(params, [case], complete, complete, judged, set(), len(load_triggers()))
        ok = bool(missing) == expect_missing
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} judgement over {','.join(sorted(mapping.values()))}: "
              f"{'counted missing' if missing else 'complete'}")
    return failures


def schema_sample(schema: Mapping[str, object], k: int) -> object:
    """A value that `schema` allows, taking the k-th value of every enum (cycling)."""
    enum, kind = schema.get("enum"), schema.get("type")
    if isinstance(enum, list):
        return enum[k % len(enum)]
    properties, items = schema.get("properties"), schema.get("items")
    if kind == "object" and isinstance(properties, dict):
        return {name: schema_sample(sub, k) for name, sub in properties.items()}
    if kind == "array" and isinstance(items, dict):
        return [schema_sample(items, k)]
    return {"string": "x", "integer": 1}[str(kind)]


def selftest_judge_schema() -> int:
    """Every output the judge schema allows must validate as a JudgeResult, or valid
    judgements would be rejected. The schema is sent to the judges and must not change."""
    errors = []
    for k in range(len(DIMENSIONS)):
        try:
            JudgeResult.model_validate(schema_sample(judge_schema(), k))
        except ValidationError as exc:
            errors.append(first_error(exc))
    print(f"{'ok  ' if not errors else 'FAIL'} judge schema and JudgeResult model agree"
          + (f": {errors[0]}" if errors else ""))
    return len(errors)


# --------------------------------------------------------------------------- judge


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


JUDGE_MODES: Mapping[str, Callable[[str], list[str]]] = {
    # Both families judge every group, which separates writer effects from judge taste.
    "panel": lambda harness: list(HARNESSES),
    # Only the other family.
    "cross": lambda harness: [HARNESS[harness].other],
}


def judges_for(harness: str, mode: str) -> list[str]:
    """The judges for one writer's groups: a mode from JUDGE_MODES, or one named harness."""
    judges = JUDGE_MODES.get(mode)
    return judges(harness) if judges else [mode]


def judge_path(run: Path, harness: str, case: str, trial_name: str, judge: str) -> Path:
    return run / "judge" / harness / case / trial_name / f"{judge}.json"


def judged_conditions(path: Path) -> set[str]:
    """Conditions a stored judgement compared. A group judged while an output was missing
    covers fewer than the run expects, and must be redone once the output exists."""
    return set(read_model(JudgedConditions, path).mapping.values()) if path.exists() else set()


def blind_mapping(seed: str, conditions: Iterable[str]) -> dict[str, str]:
    """Anonymous labels R1, R2, ... for the conditions, in an order seeded per judge."""
    order = sorted(conditions)
    random.Random(seed).shuffle(order)
    return {f"R{i}": cond for i, cond in enumerate(order, 1)}


def judge_one(group: str, judge: str, prompt: str, mapping: Mapping[str, str], n_expectations: int,
              out: Path, opts: HarnessOptions) -> str:
    error = None
    for _attempt in range(2):
        result = call(judge, prompt, opts, schema=judge_schema())
        error = result.error or judgement_error(result.structured, list(mapping), n_expectations)
        if result.ok and not error:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({"judge": judge, "judge_model": result.model, "mapping": dict(mapping),
                                       "usage": dict(result.usage), "result": result.structured}, indent=2),
                           encoding="utf-8")
            return f"{group} judged by {judge} ok"
    return f"{group} judged by {judge} FAILED ({error})"


def cmd_judge(args: argparse.Namespace, opts: HarnessOptions) -> None:
    run = run_dir(args.run_id, must_exist=True)
    judge_opts = opts.with_effort(args.judge_effort)
    cases = cases_or_exit(args.cases)
    template = read_text(JUDGE_TEMPLATE)
    tasks = []
    for harness in args.harness:
        for case in cases:
            files = case_files(case)
            for trial in range(1, args.trials + 1):
                trial_name = f"t{trial}"
                outputs = {}
                for condition in args.conditions:
                    out_dir = gen_dir(run, harness, case.name, condition, trial)
                    if generation_ok(out_dir):
                        outputs[condition] = read_text(out_dir / "output.md")
                if len(outputs) < 2:
                    continue
                for judge in judges_for(harness, args.judge_harness):
                    out = judge_path(run, harness, case.name, trial_name, judge)
                    if judged_conditions(out) != set(outputs):
                        # Each judge sees its own seeded order, so position effects do not repeat
                        # across the panel.
                        mapping = blind_mapping(f"{args.run_id}:{harness}:{case.name}:{trial_name}:{judge}", outputs)
                        prompt = build_judge_prompt(template, case, files,
                                                    {label: outputs[cond] for label, cond in mapping.items()})
                        tasks.append(partial(judge_one, f"{harness} {case.name} {trial_name}", judge, prompt,
                                             mapping, len(case.expectations), out, judge_opts))
    print(f"judge: {len(tasks)} judgement(s) to make")
    parallel(tasks, args.jobs)


# ------------------------------------------------------------------------ triggers


def stream_until(cmd: list[str], cwd: str, timeout: int,
                 detect: Callable[[object], bool]) -> tuple[bool, str, list[object]]:
    """Run cmd, parse JSONL events, stop early on detection.
    Returns (detected, how it ended, the events read)."""
    proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True)
    timer = threading.Timer(timeout, proc.kill)
    timer.start()
    events: list[object] = []
    try:
        for event in json_values(proc.stdout or ()):
            events.append(event)
            if detect(event):
                proc.kill()
                return True, "detected", events
        proc.wait()
        return False, "timeout" if not timer.is_alive() else "finished", events
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
        proc.wait()


# A tool call that loads the skill, by tool name: the text its input must contain.
SKILL_LOADS = {"Skill": SKILL_NAME, "Read": f"{SKILL_NAME}/SKILL.md"}


def claude_loaded_skill(raw: object) -> bool:
    event = parsed(ClaudeEvent, raw)
    if event is None or event.type != "assistant" or event.message is None:
        return False
    return any(block.type == "tool_use" and block.name in SKILL_LOADS
               and SKILL_LOADS[block.name] in json.dumps(block.input)
               for block in event.message.content)


def claude_discovered(events: Sequence[object]) -> bool | None:
    """Whether the last init event listed the skill; None if there was none."""
    inits = [e for e in (parsed(ClaudeEvent, raw) for raw in events)
             if e is not None and e.type == "system" and e.subtype == "init"]
    return SKILL_NAME in (inits[-1].skills or []) if inits else None


def codex_read_skill(raw: object) -> bool:
    event = parsed(CodexEvent, raw)
    item = (event.item if event else None) or {}
    return item.get("type") not in (None, "agent_message", "reasoning") and \
        f"{SKILL_NAME}/SKILL.md" in json.dumps(item)


def claude_trigger(query: str, project: str, opts: HarnessOptions) -> TriggerOutcome:
    cmd = ["claude", "-p", query, "--output-format", "stream-json", "--verbose", "--max-turns", "3",
           "--no-session-persistence", "--tools", "Skill,Read,Glob,Grep", *flag("--model", opts.claude_model)]
    triggered, ended, events = stream_until(cmd, project, opts.timeout, claude_loaded_skill)
    return TriggerOutcome(triggered=triggered, ended=ended, discovered=claude_discovered(events))


def codex_trigger(query: str, project: str, opts: HarnessOptions) -> TriggerOutcome:
    triggered, ended, _ = stream_until([*codex_base_command(opts, project), query], project, opts.timeout,
                                       codex_read_skill)
    return TriggerOutcome(triggered=triggered, ended=ended, discovered=None)


class Harness(Model):
    """What differs between the CLIs."""

    call: Callable[[str, HarnessOptions, Mapping[str, object] | None], CallResult]
    trigger: Callable[[str, str, HarnessOptions], TriggerOutcome]
    skills_dir: str  # project-level skills directory
    other: str  # the other model family


HARNESS: Mapping[str, Harness] = {
    "claude": Harness(call=call_claude, trigger=claude_trigger, skills_dir=".claude/skills", other="codex"),
    "codex": Harness(call=call_codex, trigger=codex_trigger, skills_dir=".agents/skills", other="claude"),
}


def call(harness: str, prompt: str, opts: HarnessOptions, schema: Mapping[str, object] | None = None) -> CallResult:
    return HARNESS[harness].call(prompt, opts, schema)


def trigger_one(harness: str, index: int, attempt: int, item: TriggerQuery, out: Path, opts: HarnessOptions) -> str:
    with tempfile.TemporaryDirectory(prefix="tw-trigger-") as project:
        skills_dir = Path(project) / HARNESS[harness].skills_dir
        skills_dir.mkdir(parents=True)
        (skills_dir / SKILL_NAME).symlink_to(SKILL_DIR, target_is_directory=True)
        subprocess.run(["git", "init", "-q", project], check=True)
        start = time.monotonic()
        outcome = HARNESS[harness].trigger(item.query, project, opts)
    result = TriggerRun(**outcome.model_dump(), harness=harness, index=index, attempt=attempt, query=item.query,
                        should_trigger=item.should_trigger, duration_s=round(time.monotonic() - start, 1))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result.model_dump(), indent=2), encoding="utf-8")
    mark = "hit" if outcome.triggered == item.should_trigger else "MISS"
    return f"{harness} q{index} r{attempt} triggered={outcome.triggered} {mark} ({outcome.ended})"


def cmd_triggers(args: argparse.Namespace, opts: HarnessOptions) -> None:
    if not SKILL_MD.exists():
        sys.exit(f"triggers need {SKILL_MD.relative_to(ROOT)}")
    run = run_dir(args.run_id)
    write_manifest(run, args, opts)
    trigger_opts = opts.model_copy(update={"timeout": args.trigger_timeout})
    tasks = []
    for harness in args.harness:
        for index, item in enumerate(load_triggers(), 1):
            for attempt in range(1, args.trigger_runs + 1):
                out = run / "triggers" / harness / f"q{index:02d}-r{attempt}.json"
                if not out.exists():
                    tasks.append(partial(trigger_one, harness, index, attempt, item, out, trigger_opts))
    print(f"triggers: {len(tasks)} run(s)")
    parallel(tasks, args.jobs)


# -------------------------------------------------------------------------- report


class ReportParams(Model):
    """What a run was asked to produce, from the command line."""

    run_id: str
    harnesses: tuple[str, ...]
    conditions: tuple[str, ...]
    trials: int
    judge_harness: str
    trigger_runs: int


class ConditionStats(Model):
    check_pass_rate: float | None
    check_pass_sd: float | None
    expectation_pass_rate: float | None
    expectation_unknown: int
    fidelity_pass_rate: float | None
    sensitive_leaks: int
    mean_input_tokens: float | None
    mean_output_words: float | None


class GroupCounts(Model):
    first: int
    tie: int
    second: int


class PairStats(Model):
    groups: GroupCounts
    cases_preferred: tuple[str, ...]
    net_by_case: Mapping[str, int]
    mean_case_preference: float
    ci95: tuple[float, float]


class TriggerStats(Model):
    recall: float | None
    false_positive_rate: float | None
    runs: int
    undiscovered_runs: int


class HarnessStats(Model):
    """Everything the gates read for one writer, and what report.json records."""

    conditions: Mapping[str, ConditionStats]
    overhead: Mapping[str, float] = Field(default_factory=dict)  # input tokens over A, per condition
    pairs: Mapping[str, PairStats] = Field(default_factory=dict)  # "C_vs_B" and so on
    triggers: TriggerStats | None = None


PAIRS = (("C", "B"), ("C", "A"), ("B", "A"))
VERDICT_MARKS: Mapping[int | None, str] = {1: "X", -1: "Y", 0: "tie", None: "–"}

K = TypeVar("K")
V = TypeVar("V")


def grouped(pairs: Iterable[tuple[K, V]]) -> dict[K, list[V]]:
    """Values grouped by key, keys in order of first appearance."""
    groups: dict[K, list[V]] = {}
    for key, value in pairs:
        groups.setdefault(key, []).append(value)
    return groups


def mean(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def stdev(values: Sequence[float]) -> float | None:
    return statistics.stdev(values) if len(values) > 1 else None


def fmt(value: float | None, pct: bool = True) -> str:
    if value is None:
        return "–"
    return f"{value * 100:.0f}%" if pct else f"{value:,.0f}"


def fmt_mean_sd(values: Sequence[float]) -> str:
    m, s = mean(values), stdev(values)
    return fmt(m) if s is None else f"{fmt(m)} ± {s * 100:.0f}"


def pass_rate(rows: Sequence[CheckRow]) -> float:
    return sum(r.passed for r in rows) / len(rows)


def ranks_of(judgement: StoredJudgement, first: str, second: str) -> list[tuple[int, int]] | None:
    """(rank of first, rank of second) per dimension; None if the judgement lacks either."""
    label_of = {cond: label for label, cond in judgement.mapping.items()}
    if first not in label_of or second not in label_of:
        return None
    return [(ranks[label_of[first]], ranks[label_of[second]])
            for ranks in ({r.label: r.rank for r in dim.ranks} for dim in judgement.result.dimensions)]


def group_verdict(judgement: StoredJudgement, first: str, second: str) -> int | None:
    """One verdict per judged group: +1 if `first` beats `second` on more dimensions than
    it loses, -1 for the reverse, 0 otherwise. Dimensions from one call are correlated,
    so they are not counted as separate votes."""
    ranks = ranks_of(judgement, first, second)
    if ranks is None:
        return None
    wins, losses = sum(a < b for a, b in ranks), sum(a > b for a, b in ranks)
    return (wins > losses) - (wins < losses)


def dimension_counts(judgements: Sequence[Judged], first: str, second: str) -> dict[str, list[int]]:
    counts: dict[str, list[int]] = {d: [0, 0, 0] for d in DIMENSIONS}
    for j in judgements:
        dims = j.judgement.result.dimensions
        for dim, (a, b) in zip(dims, ranks_of(j.judgement, first, second) or []):
            counts[dim.dimension][0 if a < b else 1 if a == b else 2] += 1
    return counts


def bootstrap_ci(per_case: Mapping[str, Sequence[int]], seed: str, rounds: int = 2000) -> tuple[float, float, float]:
    """Mean per-case preference (-1..1) with a 95% bootstrap interval, resampling cases."""
    case_means = [statistics.fmean(v) for v in per_case.values()]
    rng = random.Random(seed)
    stats = sorted(statistics.fmean(rng.choice(case_means) for _ in case_means) for _ in range(rounds))
    return statistics.fmean(case_means), stats[int(0.025 * rounds)], stats[int(0.975 * rounds) - 1]


GroupKey = tuple[str, str, str, int]  # harness, case, condition, trial
JudgeKey = tuple[str, str, str, str]  # harness, case, trial name, judge


def missing_results(params: ReportParams, cases: Sequence[str], generated: set[GroupKey], checked: set[GroupKey],
                    judged: Mapping[JudgeKey, set[str]], trigger_runs: set[tuple[str, int, int]],
                    n_queries: int) -> list[str]:
    """Everything the documented gates need, given this run's parameters."""
    def missing() -> Iterator[str]:
        for harness in params.harnesses:
            for case in cases:
                for trial in range(1, params.trials + 1):
                    for cond in params.conditions:
                        key = (harness, case, cond, trial)
                        if key not in generated:
                            yield f"generation {harness}/{case}/{cond}/t{trial}"
                        elif key not in checked:
                            yield f"checks {harness}/{case}/{cond}/t{trial}"
                    for judge in judges_for(harness, params.judge_harness):
                        seen = judged.get((harness, case, f"t{trial}", judge), set())
                        if seen != set(params.conditions):
                            partial = f" (saw only {','.join(sorted(seen))})" if seen else ""
                            yield f"judgement {harness}/{case}/t{trial} by {judge}{partial}"
            for index in range(1, n_queries + 1):
                for attempt in range(1, params.trigger_runs + 1):
                    if (harness, index, attempt) not in trigger_runs:
                        yield f"trigger {harness}/q{index}/r{attempt}"
    return list(missing())


def writer_line(metas: Sequence[GenerationMeta], judgements: Sequence[Judged]) -> str:
    models = sorted({m.model or "default" for m in metas})
    judges = sorted({f"{j.judgement.judge} ({j.judgement.judge_model or 'default'})" for j in judgements})
    return (f"Writer model: {', '.join(models) or '–'}. Judges: {', '.join(judges) or 'none'}. "
            f"Failed generations: {sum(not m.ok for m in metas)}.")


def response_for(judged: Judged, condition: str) -> ResponseGrade | None:
    label = next((l for l, c in judged.judgement.mapping.items() if c == condition), None)
    return next((r for r in judged.judgement.result.responses if r.label == label), None)


def condition_stats(cond: str, checks: Sequence[CheckRow], judgements: Sequence[Judged],
                    metas: Sequence[GenerationMeta]) -> tuple[ConditionStats, list[float]]:
    """A condition's row, and its per-trial check pass rates."""
    c_checks = [c for c in checks if c.condition == cond]
    per_trial = [pass_rate([c for c in c_checks if c.trial == trial]) for trial in sorted({c.trial for c in c_checks})]
    responses = [r for r in (response_for(j, cond) for j in judgements) if r is not None]
    verdicts = [e.verdict for r in responses for e in r.expectations]
    known = [v for v in verdicts if v != "unknown"]
    c_metas = [m for m in metas if m.condition == cond and m.ok]
    return ConditionStats(
        check_pass_rate=mean(per_trial), check_pass_sd=stdev(per_trial),
        expectation_pass_rate=(sum(v == "pass" for v in known) / len(known)) if known else None,
        expectation_unknown=verdicts.count("unknown"),
        fidelity_pass_rate=mean([float(r.fidelity == "pass") for r in responses]),
        sensitive_leaks=sum(1 for c in c_checks if c.check in SENSITIVE_CHECKS and not c.passed),
        mean_input_tokens=mean([m.usage.get("input_tokens", 0) or 0 for m in c_metas]),
        mean_output_words=mean([m.output_words for m in c_metas]),
    ), per_trial


def condition_table(conditions: Sequence[str], checks: Sequence[CheckRow], judgements: Sequence[Judged],
                    metas: Sequence[GenerationMeta]) -> tuple[list[str], dict[str, ConditionStats]]:
    lines = ["| Condition | Checks passed (mean ± sd over trials) | Expectations passed (judges) "
             "| Fidelity passed (judges) | Sensitive leaks | Input tokens | Output words |",
             "|---|---|---|---|---|---|---|"]
    stats: dict[str, ConditionStats] = {}
    for cond in conditions:
        row, per_trial = condition_stats(cond, checks, judgements, metas)
        stats[cond] = row
        lines.append(f"| {cond} ({CONDITIONS[cond]}) | {fmt_mean_sd(per_trial)} "
                     f"| {fmt(row.expectation_pass_rate)} | {fmt(row.fidelity_pass_rate)} "
                     f"| {row.sensitive_leaks} | {fmt(row.mean_input_tokens, False)} "
                     f"| {fmt(row.mean_output_words, False)} |")
    return lines + [""], stats


def guidance_overhead(stats: Mapping[str, ConditionStats]) -> dict[str, float]:
    """Measured input tokens of B and C over A."""
    base = stats["A"].mean_input_tokens if "A" in stats else None
    tokens = {cond: stats[cond].mean_input_tokens for cond in ("B", "C") if cond in stats}
    return {cond: t - base for cond, t in tokens.items() if base and t}


def case_check_table(cases: Sequence[str], conditions: Sequence[str], checks: Sequence[CheckRow]) -> list[str]:
    def cell(case: str, cond: str) -> str:
        rows = [c for c in checks if c.case == case and c.condition == cond]
        return fmt(pass_rate(rows)) if rows else "–"

    def failing(case: str) -> str:
        return ", ".join(sorted({c.check for c in checks if c.case == case and c.condition == "C" and not c.passed}))

    return (["| Case | " + " | ".join(f"{c} checks" for c in conditions) + " | Failing checks under C |",
             "|---|" + "---|" * len(conditions) + "---|"]
            + [f"| {case} | " + " | ".join(cell(case, cond) for cond in conditions) + f" | {failing(case) or '–'} |"
               for case in cases]
            + [""])


def pair_section(run_id: str, harness: str, first: str, second: str,
                 judgements: Sequence[Judged]) -> tuple[list[str], PairStats | None]:
    judges = sorted({j.judgement.judge for j in judgements})
    verdicts = {judge: [(j.case, v) for j in judgements if j.judgement.judge == judge
                        if (v := group_verdict(j.judgement, first, second)) is not None]
                for judge in judges}
    tallies = {judge: [sum(v == want for _, v in found) for want in (1, 0, -1)] for judge, found in verdicts.items()}
    pooled = [sum(t[i] for t in tallies.values()) for i in range(3)]
    per_case = grouped(pair for judge in judges for pair in verdicts[judge])
    if not per_case:
        return [], None
    preferred = tuple(sorted(k for k, v in per_case.items() if sum(v) > 0))
    point, low, high = bootstrap_ci(per_case, f"{run_id}:{harness}:{first}{second}")
    stats = PairStats(groups=GroupCounts(first=pooled[0], tie=pooled[1], second=pooled[2]),
                      cases_preferred=preferred, net_by_case={k: sum(v) for k, v in per_case.items()},
                      mean_case_preference=point, ci95=(low, high))
    lines = [f"Judge verdicts, {first} vs {second}, one per judged group:", "",
             f"| Judge | {first} preferred | Tie | {second} preferred |", "|---|---|---|---|",
             *[f"| {judge} | {t[0]} | {t[1]} | {t[2]} |" for judge, t in tallies.items()],
             f"| pooled | {pooled[0]} | {pooled[1]} | {pooled[2]} |", "",
             f"{first} net-preferred on {len(preferred)} of {len(per_case)} cases"
             + (f" ({', '.join(preferred)})" if preferred else "")
             + f". Mean per-case preference {point:+.2f} on a −1 to +1 scale, "
             f"95% bootstrap interval {low:+.2f} to {high:+.2f}.", ""]
    if (first, second) == ("C", "B"):
        dims = dimension_counts(judgements, "C", "B")
        lines += ["C vs B by dimension (descriptive; dimensions from one call are correlated):", "",
                  "| Dimension | C better | Tie | B better |", "|---|---|---|---|",
                  *[f"| {d} | {w} | {t} | {l} |" for d, (w, t, l) in dims.items()], ""]
    return lines, stats


def position_audit(judgements: Sequence[Judged], n_conditions: int) -> list[str]:
    def firsts(judge: str) -> list[float]:
        return [float(min(r.rank for r in d.ranks) == next(r.rank for r in d.ranks if r.label == "R1"))
                for j in judgements if j.judgement.judge == judge for d in j.judgement.result.dimensions]

    return [f"Position audit, {judge}: first-shown response ranked best (ties included) in "
            f"{fmt(mean(firsts(judge)))} of dimension judgements; at least {fmt(1 / n_conditions)} "
            f"is expected without bias. With few groups this is confounded with which condition "
            f"happened to be shown first."
            for judge in sorted({j.judgement.judge for j in judgements})] + [""]


def trigger_section(runs: Sequence[TriggerRun]) -> tuple[list[str], TriggerStats]:
    pos = [float(t.triggered) for t in runs if t.should_trigger]
    neg = [float(t.triggered) for t in runs if not t.should_trigger]
    undiscovered = sum(1 for t in runs if t.discovered is False)
    stats = TriggerStats(recall=mean(pos), false_positive_rate=mean(neg), runs=len(runs),
                         undiscovered_runs=undiscovered)

    def query_row(index: int) -> str:
        found = [t for t in runs if t.index == index]
        rate = statistics.fmean(float(t.triggered) for t in found)
        flag = "" if (rate >= 0.5) == found[0].should_trigger else " ⚠"
        query = found[0].query[:90] + ("…" if len(found[0].query) > 90 else "")
        return f"| {index} | {'yes' if found[0].should_trigger else 'no'} | {fmt(rate)}{flag} | {query} |"

    lines = [f"Triggering: recall {fmt(mean(pos))} on should-trigger queries, false-positive rate "
             f"{fmt(mean(neg))} on near misses ({len(runs)} runs"
             + (f"; skill not discovered in {undiscovered} runs" if undiscovered else "") + ").", "",
             "| # | Expected | Trigger rate | Query |", "|---|---|---|---|",
             *[query_row(index) for index in sorted({t.index for t in runs})], ""]
    return lines, stats


def harness_section(params: ReportParams, harness: str, cases: Sequence[str], conditions: Sequence[str],
                    metas: Sequence[GenerationMeta], checks: Sequence[CheckRow], judgements: Sequence[Judged],
                    triggers: Sequence[TriggerRun]) -> tuple[list[str], HarnessStats]:
    h_metas = [m for m in metas if m.harness == harness]
    h_checks = [c for c in checks if c.harness == harness]
    h_judge = [j for j in judgements if j.harness == harness]
    h_trig = [t for t in triggers if t.harness == harness]
    table, stats = condition_table(conditions, h_checks, h_judge, h_metas)
    overhead = guidance_overhead(stats)
    pairs = {f"{first}_vs_{second}": pair_section(params.run_id, harness, first, second, h_judge)
             for first, second in PAIRS}
    trig_lines, trig_stats = trigger_section(h_trig) if h_trig else ([], None)
    lines = [f"## {harness}", "", writer_line(h_metas, h_judge), "", *table,
             *[f"Measured guidance overhead, {cond} vs A: {tokens:,.0f} input tokens per call."
               for cond, tokens in overhead.items()], "",
             *(case_check_table(cases, conditions, h_checks) if h_checks else []),
             *[line for pair_lines, _ in pairs.values() for line in pair_lines],
             *position_audit(h_judge, len(conditions)),
             *trig_lines]
    return lines, HarnessStats(conditions=stats, overhead=overhead, triggers=trig_stats,
                               pairs={name: s for name, (_, s) in pairs.items() if s is not None})


def harness_json(stats: HarnessStats) -> dict[str, object]:
    """report.json's entry for one writer, in the order earlier reports used."""
    conditions = {cond: {**row.model_dump(), **({"guidance_overhead_tokens": stats.overhead[cond]}
                                                if cond in stats.overhead else {})}
                  for cond, row in stats.conditions.items()}
    return {"conditions": conditions, **{name: pair.model_dump() for name, pair in stats.pairs.items()},
            **({"triggers": stats.triggers.model_dump()} if stats.triggers else {})}


Gate = tuple[str, Callable[[int, int], str], Callable[[HarnessStats | None, int], bool | None]]


def rates_ok(stats: HarnessStats | None, rate: Callable[[ConditionStats], float | None]) -> bool | None:
    b, c = (stats.conditions.get(k) if stats else None for k in ("B", "C"))
    rate_b, rate_c = (rate(b) if b else None), (rate(c) if c else None)
    return None if rate_b is None or rate_c is None else rate_c >= rate_b - FIDELITY_TOLERANCE


def pair_ok(stats: HarnessStats | None, test: Callable[[PairStats], bool]) -> bool | None:
    pair = stats.pairs.get("C_vs_B") if stats else None
    return None if not pair else test(pair)


def triggers_ok(stats: HarnessStats | None, test: Callable[[TriggerStats], bool]) -> bool | None:
    return None if not (stats and stats.triggers) else test(stats.triggers)


# The pre-registered release gates from evals/README.md: (kind, name, test per harness).
GATES: Sequence[Gate] = (
    ("safety", lambda needed, n: "no sensitive leaks under C",
     lambda h, needed: None if not (h and "C" in h.conditions) else h.conditions["C"].sensitive_leaks == 0),
    ("fidelity", lambda needed, n: f"C checks ≥ B checks − {FIDELITY_TOLERANCE:.0%}",
     lambda h, needed: rates_ok(h, lambda row: row.check_pass_rate)),
    ("fidelity", lambda needed, n: f"C judge fidelity ≥ B − {FIDELITY_TOLERANCE:.0%}",
     lambda h, needed: rates_ok(h, lambda row: row.fidelity_pass_rate)),
    ("value", lambda needed, n: "judges prefer C over B in more groups than the reverse",
     lambda h, needed: pair_ok(h, lambda p: p.groups.first > p.groups.second)),
    ("value", lambda needed, n: f"C net-preferred over B on ≥ {needed}/{n} cases",
     lambda h, needed: pair_ok(h, lambda p: len(p.cases_preferred) >= needed)),
    ("trigger", lambda needed, n: f"trigger recall ≥ {MIN_TRIGGER_RECALL:.0%}",
     lambda h, needed: triggers_ok(h, lambda t: t.recall is not None and t.recall >= MIN_TRIGGER_RECALL)),
    ("trigger", lambda needed, n: f"trigger false positives ≤ {MAX_TRIGGER_FALSE_POSITIVE:.0%}",
     lambda h, needed: triggers_ok(h, lambda t: t.false_positive_rate is not None
                                   and t.false_positive_rate <= MAX_TRIGGER_FALSE_POSITIVE)),
)

GateResult = tuple[str, str, bool | None]  # kind, name, outcome


def failed(*kinds: str) -> Callable[[Sequence[GateResult], Sequence[str]], bool]:
    return lambda results, missing: not all(ok for kind, _, ok in results if kind in kinds)


# The first verdict whose condition holds wins. Fails closed: any missing result, or any gate
# that could not be evaluated for a harness, makes the run INCOMPLETE.
VERDICTS: Sequence[tuple[Callable[[Sequence[GateResult], Sequence[str]], bool], str]] = (
    (lambda results, missing: bool(missing) or not results or any(ok is None for _, _, ok in results),
     "INCOMPLETE: the run is missing results the gates need; no release decision."),
    (failed("safety", "fidelity"), "FIX: the skill loses fidelity or safety relative to the short rule."),
    (failed("value"), "SIMPLIFY: the short rule performs about as well; prefer it over the skill."),
    (failed("trigger"), "FIX DESCRIPTION: content passes but triggering misses the thresholds."),
    (lambda results, missing: True, "SHIP: every gate passes for every harness."),
)


def gates(stats: Mapping[str, HarnessStats], n_cases: int, harnesses: Sequence[str],
          missing: Sequence[str]) -> tuple[list[str], dict[str, bool | None], str]:
    """Apply the release gates: report lines, each gate's outcome by name, and the verdict."""
    needed = math.ceil(n_cases / 2)
    results = [(kind, f"{harness}: {name(needed, n_cases)}", test(stats.get(harness), needed))
               for harness in harnesses for kind, name, test in GATES]
    verdict = next(text for applies, text in VERDICTS if applies(results, missing))
    mark = {True: "PASS", False: "FAIL", None: "NOT EVALUATED"}
    lines = ["## Release gates", "", *[f"- {mark[ok]}: {name}" for _, name, ok in results]]
    if missing:
        lines += ["", f"Missing results ({len(missing)}): " + "; ".join(missing[:10])
                  + ("; …" if len(missing) > 10 else "")]
    return lines + ["", f"**Verdict:** {verdict}", ""], {name: ok for _, name, ok in results}, verdict


def report(params: ReportParams, cases: Sequence[str], metas: Sequence[GenerationMeta], checks: Sequence[CheckRow],
           judgements: Sequence[Judged], triggers: Sequence[TriggerRun], n_queries: int) -> tuple[list[str], dict[str, object]]:
    """report.md's lines and report.json's content."""
    conditions = [c for c in CONDITIONS if c in params.conditions]
    manifest = (RUNS_DIR / params.run_id / "manifest.json").relative_to(ROOT)
    lines = [f"# Eval report: {params.run_id}", "",
             f"Manifest: `{manifest}`. Expected: {len(cases)} cases × "
             f"{len(conditions)} conditions × {params.trials} trials per harness ({', '.join(params.harnesses)}), "
             f"judges: {params.judge_harness}, {params.trigger_runs} trigger runs per query.", ""]
    stats: dict[str, HarnessStats] = {}
    for harness in params.harnesses:
        section, stats[harness] = harness_section(params, harness, cases, conditions, metas, checks, judgements, triggers)
        lines += section
    missing = missing_results(
        params, cases,
        generated={(m.harness, m.case, m.condition, m.trial) for m in metas if m.ok},
        checked={(c.harness, c.case, c.condition, c.trial) for c in checks},
        judged={(j.harness, j.case, j.trial, j.judge): set(j.judgement.mapping.values()) for j in judgements},
        trigger_runs={(t.harness, t.index, t.attempt) for t in triggers},
        n_queries=n_queries)
    gate_lines, results, verdict = gates(stats, len(cases), params.harnesses, missing)
    summary: dict[str, object] = {"run_id": params.run_id, "harnesses": {h: harness_json(s) for h, s in stats.items()},
                                  "gates": results, "missing": missing, "verdict": verdict}
    return lines + gate_lines, summary


def report_params(args: argparse.Namespace) -> ReportParams:
    return ReportParams(run_id=args.run_id, harnesses=tuple(args.harness), conditions=tuple(args.conditions),
                        trials=args.trials, judge_harness=args.judge_harness, trigger_runs=args.trigger_runs)


def load_judgements(run: Path) -> list[Judged]:
    return [Judged(harness=path.parts[-4], case=path.parts[-3], trial=path.parts[-2], judge=path.stem,
                   judgement=read_model(StoredJudgement, path))
            for path in sorted((run / "judge").glob("*/*/t*/*.json"))]


def cmd_report(args: argparse.Namespace) -> int:
    run = run_dir(args.run_id, must_exist=True)
    params = report_params(args)
    cases = [c.name for c in cases_or_exit(args.cases)]
    metas = [m for m in (read_model(GenerationMeta, p) for p in sorted((run / "gen").glob("*/*/*/t*/meta.json")))
             if m.harness in params.harnesses and m.case in cases and m.condition in params.conditions]
    stored = CHECK_ROWS.validate_json(read_text(run / "checks.json")) if (run / "checks.json").exists() else ()
    checks = [c for c in stored
              if c.harness in params.harnesses and c.case in cases and c.condition in params.conditions]
    judgements = [j for j in load_judgements(run) if j.harness in params.harnesses and j.case in cases]
    triggers = [t for t in (read_model(TriggerRun, p) for p in sorted((run / "triggers").glob("*/q*.json")))
                if t.harness in params.harnesses]
    lines, summary = report(params, cases, metas, checks, judgements, triggers, len(load_triggers()))
    (run / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (run / "report.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\n".join(lines))
    return 0


# ---------------------------------------------------------------------------- lint

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
        (name != SKILL_DIR.name, f"name {name!r} does not match directory {SKILL_DIR.name!r}"),
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


T = TypeVar("T")


def load_or_error(name: str, load: Callable[[], Sequence[T]]) -> tuple[Sequence[T], list[str]]:
    try:
        return load(), []
    except ValidationError as exc:
        return (), [f"{name}: {first_error(exc)}"]


def cmd_lint(_args: argparse.Namespace) -> int:
    errors: list[str] = []
    warnings: list[str] = []
    if SKILL_MD.exists():
        text = read_text(SKILL_MD)
        broken = [t for t in relative_links(split_frontmatter(text)[1]) if not (SKILL_DIR / t).exists()]
        yaml = SKILL_DIR / "agents" / "openai.yaml"
        skill_errors, skill_warnings, summary = lint_skill(text, broken, read_text(yaml) if yaml.exists() else None)
        errors, warnings = errors + skill_errors, warnings + skill_warnings
        print(summary)
    else:
        warnings.append(f"{SKILL_MD.relative_to(ROOT)} does not exist yet; condition C and triggers will not run")
    cases, case_errors = load_or_error("cases.json", load_cases)
    triggers, trigger_errors = load_or_error("triggers.json", load_triggers)
    missing = {f for case in cases for f in case.files if not (ROOT / f).exists()}
    data_errors, data_warnings, summary = lint_data(cases, missing, triggers)
    errors, warnings = errors + case_errors + data_errors + trigger_errors, warnings + data_warnings
    print(summary)
    for w in warnings:
        print(f"warning: {w}")
    for e in errors:
        print(f"error: {e}")
    print("lint: ok" if not errors else f"lint: {len(errors)} error(s)")
    return 1 if errors else 0


# ------------------------------------------------------------------------- calibrate

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


def cmd_calibrate(args: argparse.Namespace) -> int:
    """Write a blind packet for the human judge check: one random group per case, the skill
    (C) and the short rule (B) shown as X and Y in random order, and a separate answer key."""
    run = run_dir(args.run_id, must_exist=True)
    cases = load_cases()
    complete = {(case.name, trial) for case in cases for trial in range(1, args.trials + 1)
                if all((gen_dir(run, args.harness, case.name, c, trial) / "output.md").exists() for c in ("B", "C"))
                and all(judge_path(run, args.harness, case.name, f"t{trial}", j).exists() for j in HARNESSES)}
    picks = calibration_picks(args.run_id, args.harness, cases, args.trials, args.groups, complete)
    packet, key = packet_header(args.run_id, args.harness), key_header(args.run_id, args.harness)
    for number, pick in enumerate(picks, 1):
        case, trial = pick[0], pick[1]
        outputs = {c: read_text(gen_dir(run, args.harness, case.name, c, trial) / "output.md") for c in ("B", "C")}
        judgements = [read_model(StoredJudgement, judge_path(run, args.harness, case.name, f"t{trial}", j))
                      for j in HARNESSES]
        section, row = calibration_group(number, pick, case_files(case), outputs, judgements)
        packet, key = packet + section, key + [row]
    out = run / "calibration"
    out.mkdir(exist_ok=True)
    (out / "packet.md").write_text("\n".join(packet), encoding="utf-8")
    (out / "key.md").write_text("\n".join(key) + "\n", encoding="utf-8")
    print(f"calibrate: {len(picks)} group(s) -> {(out / 'packet.md').relative_to(ROOT)}, "
          f"key in {(out / 'key.md').relative_to(ROOT)}")
    return 0 if len(picks) == args.groups else 1


# ----------------------------------------------------------------------------- cli


def run_dir(run_id: str, must_exist: bool = False) -> Path:
    path = RUNS_DIR / run_id
    if must_exist and not path.exists():
        sys.exit(f"no run at {path.relative_to(ROOT)}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def tool_version(cmd: list[str]) -> str | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def write_manifest(run: Path, args: argparse.Namespace, opts: HarnessOptions) -> None:
    path = run / "manifest.json"
    raw = json.loads(read_text(path)) if path.exists() else {"created": dt.datetime.now(dt.timezone.utc).isoformat()}
    manifest = Manifest.model_validate(raw)
    invocation = Invocation(
        at=dt.datetime.now(dt.timezone.utc).isoformat(),
        argv=sys.argv[1:],
        git_commit=tool_version(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"]),
        git_dirty=bool(tool_version(["git", "-C", str(ROOT), "status", "--porcelain"])),
        skill_sha=sha256(read_text(SKILL_MD)) if SKILL_MD.exists() else None,
        short_rule_sha=sha256(read_text(SHORT_RULE_FILE)),
        cases_sha=sha256(read_text(CASES_FILE)),
        claude_version=tool_version(["claude", "--version"]) if shutil.which("claude") else None,
        codex_version=tool_version(["codex", "--version"]) if shutil.which("codex") else None,
        options=opts.model_dump(),
    )
    updated = {**raw, "invocations": [*manifest.invocations, invocation.model_dump()]}
    path.write_text(json.dumps(updated, indent=2), encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("lint", help="validate the skill and eval data")
    sub.add_parser("selftest", help="check the graders against reference solutions and raw inputs")
    for name in ("generate", "check", "judge", "triggers", "report", "all"):
        p = sub.add_parser(name)
        p.add_argument("--run-id", default=dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
        p.add_argument("--harness", default="claude,codex", type=lambda s: s.split(","))
        p.add_argument("--cases", default="all", help="comma-separated case names or ids")
        p.add_argument("--conditions", default="A,B,C", type=lambda s: s.split(","))
        p.add_argument("--trials", type=int, default=3)
        p.add_argument("--jobs", type=int, default=4)
        p.add_argument("--judge-harness", default="panel", choices=["panel", "cross", *HARNESSES])
        p.add_argument("--trigger-runs", type=int, default=3)
        p.add_argument("--trigger-timeout", type=int, default=180)
        p.add_argument("--timeout", type=int, default=600, help="seconds per generation or judge call")
        p.add_argument("--claude-model")
        p.add_argument("--claude-effort")
        p.add_argument("--codex-model")
        p.add_argument("--codex-effort")
        p.add_argument("--judge-effort", help="reasoning effort for judge calls (either harness)")
    p = sub.add_parser("calibrate", help="write a blind packet for the human judge check")
    p.add_argument("--run-id", required=True)
    p.add_argument("--harness", default="claude", choices=HARNESSES, help="writer whose groups to sample")
    p.add_argument("--groups", type=int, default=5)
    p.add_argument("--trials", type=int, default=3)
    args = parser.parse_args(argv)
    if args.command == "calibrate":
        return args
    for value in getattr(args, "harness", []):
        if value not in HARNESSES:
            parser.error(f"unknown harness {value}")
    for value in getattr(args, "conditions", []):
        if value not in CONDITIONS:
            parser.error(f"unknown condition {value}")
    return args


# Commands that need no harness options, and the steps of a run, in the order `all` runs them.
COMMANDS: Mapping[str, Callable[[argparse.Namespace], int]] = {
    "lint": cmd_lint, "selftest": cmd_selftest, "calibrate": cmd_calibrate,
}
STEPS: Mapping[str, Callable[[argparse.Namespace, HarnessOptions], object]] = {
    "generate": cmd_generate,
    "check": lambda args, _: cmd_check(args),
    "judge": cmd_judge,
    "triggers": cmd_triggers,
    "report": lambda args, _: cmd_report(args),
}


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.command in COMMANDS:
        return COMMANDS[args.command](args)
    opts = HarnessOptions(claude_model=args.claude_model, claude_effort=args.claude_effort,
                          codex_model=args.codex_model, codex_effort=args.codex_effort, timeout=args.timeout)
    for step in (STEPS if args.command == "all" else [args.command]):
        STEPS[step](args, opts)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
