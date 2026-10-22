"""Files and processes: where the eval data and runs live, reading them, a run's directory layout
and manifest, and running the claude and codex CLIs."""

from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TypeVar

from pydantic import ValidationError

from .harness import (
    claude_command, claude_discovered, claude_loaded_skill, claude_trigger_command, codex_base_command,
    codex_exec_command, codex_read_skill, json_values, parse_claude_reply, parse_codex_output,
)
from .models import (
    SKILL_NAME, TRIGGER_QUERIES, M, CallResult, Case, CaseFile, GenerationMeta, Guidance, HarnessOptions,
    Invocation, Judged, JudgedConditions, Manifest, Model, StoredJudgement, TriggerOutcome, TriggerQuery,
    first_error,
)
from .prompts import sha256, split_frontmatter

ROOT = Path(__file__).resolve().parents[2]
EVALS = ROOT / "evals"
SKILL_DIR = ROOT / "skills" / SKILL_NAME
SKILL_MD = SKILL_DIR / "SKILL.md"
SUITE_DIR = EVALS / "suites" / SKILL_NAME  # the skill's eval data; case files are relative to it
CASES_FILE = SUITE_DIR / "cases.json"
TRIGGERS_FILE = SUITE_DIR / "triggers.json"
SHORT_RULE_FILE = SUITE_DIR / "conditions" / "short-rule.md"
JUDGE_TEMPLATE = SUITE_DIR / "judge.md"
REFERENCE_DIR = SUITE_DIR / "reference"
RUNS_DIR = EVALS / "runs" / SKILL_NAME


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
    return [(Path(f).name, read_text(SUITE_DIR / f)) for f in case.files]


def load_guidance() -> Guidance:
    body = split_frontmatter(read_text(SKILL_MD))[1].strip() if SKILL_MD.exists() else None
    return Guidance(short_rule=read_text(SHORT_RULE_FILE).strip(), skill_body=body)


T = TypeVar("T")


def load_or_error(name: str, load: Callable[[], Sequence[T]]) -> tuple[Sequence[T], list[str]]:
    try:
        return load(), []
    except ValidationError as exc:
        return (), [f"{name}: {first_error(exc)}"]


def gen_dir(run: Path, harness: str, case: str, condition: str, trial: int) -> Path:
    return run / "gen" / harness / case / condition / f"t{trial}"


def generation_ok(out: Path) -> bool:
    meta = out / "meta.json"
    return meta.exists() and read_model(GenerationMeta, meta).ok


def judge_path(run: Path, harness: str, case: str, trial_name: str, judge: str) -> Path:
    return run / "judge" / harness / case / trial_name / f"{judge}.json"


def judged_conditions(path: Path) -> set[str]:
    """Conditions a stored judgement compared. A group judged while an output was missing
    covers fewer than the run expects, and must be redone once the output exists."""
    return set(read_model(JudgedConditions, path).mapping.values()) if path.exists() else set()


def load_judgements(run: Path) -> list[Judged]:
    return [Judged(harness=path.parts[-4], case=path.parts[-3], trial=path.parts[-2], judge=path.stem,
                   judgement=read_model(StoredJudgement, path))
            for path in sorted((run / "judge").glob("*/*/t*/*.json"))]


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


def write_manifest(run: Path, opts: HarnessOptions) -> None:
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


def parallel(tasks: Sequence[Callable[[], str]], jobs: int) -> None:
    total = len(tasks)
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = [pool.submit(task) for task in tasks]
        for done, future in enumerate(as_completed(futures), 1):
            print(f"[{done}/{total}] {future.result()}", flush=True)
