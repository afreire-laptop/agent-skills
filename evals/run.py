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
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from pathlib import Path
from typing import TypeVar

from pydantic import ValidationError

from runner.checks import check_rows, input_word_count, regex_flags, run_check, word_count
from runner.gates import gates, missing_results
from runner.harness import (
    claude_command, claude_discovered, claude_loaded_skill, claude_trigger_command, codex_base_command,
    codex_exec_command, codex_read_skill, json_values, judges_for, parse_claude_reply, parse_codex_output,
)
from runner.models import (
    CHECK_ROWS, CONDITIONS, DIMENSIONS, HARNESSES, SKILL_NAME, TRIGGER_QUERIES, M, CallResult, Case, CaseFile,
    Check, CheckRow, ConditionStats, GenerationMeta, GroupCounts, Guidance, HarnessOptions, HarnessStats,
    Invocation, JudgedConditions, Judged, JudgeResult, Manifest, Model, PairStats, PatternCheck, ReportParams,
    StoredJudgement, TriggerOutcome, TriggerQuery, TriggerRun, TriggerStats, first_error,
)
from runner.prompts import (
    GUIDANCE, blind_mapping, build_judge_prompt, build_prompt, judge_schema, judgement_error, sha256,
    split_frontmatter,
)
from runner.report import group_verdict, report

ROOT = Path(__file__).resolve().parent.parent
EVALS = ROOT / "evals"
SKILL_DIR = ROOT / "skills" / SKILL_NAME
SKILL_MD = SKILL_DIR / "SKILL.md"
CASES_FILE = EVALS / "cases.json"
TRIGGERS_FILE = EVALS / "triggers.json"
SHORT_RULE_FILE = EVALS / "conditions" / "short-rule.md"
JUDGE_TEMPLATE = EVALS / "judge.md"
RUNS_DIR = EVALS / "runs"


DESCRIPTION_BUDGET_CHARS = 300  # own budget; the spec allows 1024
BODY_BUDGET_TOKENS = 2500  # estimated as characters / 4


# --------------------------------------------------------------------------- data


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def read_model(model: type[M], path: Path) -> M:
    return model.model_validate_json(read_text(path))


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


def load_guidance() -> Guidance:
    body = split_frontmatter(read_text(SKILL_MD))[1].strip() if SKILL_MD.exists() else None
    return Guidance(short_rule=read_text(SHORT_RULE_FILE).strip(), skill_body=body)


# ------------------------------------------------------------------------ harnesses


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


def judge_path(run: Path, harness: str, case: str, trial_name: str, judge: str) -> Path:
    return run / "judge" / harness / case / trial_name / f"{judge}.json"


def judged_conditions(path: Path) -> set[str]:
    """Conditions a stored judgement compared. A group judged while an output was missing
    covers fewer than the run expects, and must be redone once the output exists."""
    return set(read_model(JudgedConditions, path).mapping.values()) if path.exists() else set()


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


def claude_trigger(query: str, project: str, opts: HarnessOptions) -> TriggerOutcome:
    triggered, ended, events = stream_until(claude_trigger_command(query, opts), project, opts.timeout,
                                            claude_loaded_skill)
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


HARNESS: Mapping[str, Harness] = {
    "claude": Harness(call=call_claude, trigger=claude_trigger, skills_dir=".claude/skills"),
    "codex": Harness(call=call_codex, trigger=codex_trigger, skills_dir=".agents/skills"),
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


VERDICT_MARKS: Mapping[int | None, str] = {1: "X", -1: "Y", 0: "tie", None: "–"}


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
    lines, summary = report(params, cases, metas, checks, judgements, triggers, len(load_triggers()),
                            manifest=(run / "manifest.json").relative_to(ROOT))
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
