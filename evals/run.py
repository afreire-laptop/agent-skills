#!/usr/bin/env python3
"""Evaluation runner for the technical-writing skill.

Standard library only (Python 3.10+). Drives the `claude` and `codex` CLIs with
whatever login they already have, so no API keys are needed.

Subcommands:
  lint      validate the skill's frontmatter and the eval data files
  selftest  check the graders: references pass every check, raw inputs fail some
  generate  run each case under conditions A (no guidance), B (short rule),
            C (skill) for N trials per harness
  check     apply the deterministic checks in cases.json to every output
  judge     blind, cross-family LLM grading of each (case, trial) group
  triggers  measure whether the skill's description triggers on the queries
            in triggers.json
  report    aggregate everything into report.md / report.json with gates
  all       generate, check, judge, triggers, report

Runs are resumable: finished units are skipped, failed ones are retried.
See evals/README.md for the protocol and the meaning of each metric.
"""

from __future__ import annotations

import argparse
import dataclasses
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
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

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
DIMENSIONS = ("clarity", "naturalness", "brevity", "conventions")
CHECK_TYPES = {"contains", "not_contains", "regex", "not_regex", "max_word_ratio"}
CHECK_TARGETS = {"output", "message", "header"}
SENSITIVE_CHECKS = {"no-password", "no-vault-token", "no-customer-email"}

# Pre-registered thresholds (see evals/README.md, "Release gates").
DESCRIPTION_BUDGET_CHARS = 300  # own budget; the spec allows 1024
BODY_BUDGET_TOKENS = 2500  # estimated as characters / 4
FIDELITY_TOLERANCE = 0.02  # C may trail B by at most this on pass rates
MIN_TRIGGER_RECALL = 0.8
MAX_TRIGGER_FALSE_POSITIVE = 0.2

NO_REPO_NOTE = "Everything you need is included below; there is no repository checkout to inspect."


# --------------------------------------------------------------------------- data


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_cases() -> list[dict]:
    return load_json(CASES_FILE)["evals"]


def select_cases(spec: str) -> list[dict]:
    cases = load_cases()
    if spec == "all":
        return cases
    wanted = {s.strip() for s in spec.split(",")}
    chosen = [c for c in cases if c["name"] in wanted or str(c["id"]) in wanted]
    missing = wanted - {c["name"] for c in chosen} - {str(c["id"]) for c in chosen}
    if missing:
        sys.exit(f"unknown case(s): {', '.join(sorted(missing))}")
    return chosen


def case_files(case: dict) -> list[tuple[str, str]]:
    return [(Path(f).name, (ROOT / f).read_text(encoding="utf-8")) for f in case["files"]]


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def split_frontmatter(text: str) -> tuple[dict, str]:
    """Parse the simple YAML frontmatter used by SKILL.md (scalars and one-level maps)."""
    match = re.match(r"\A---\n(.*?)\n---\n?(.*)\Z", text, re.S)
    if not match:
        return {}, text
    meta: dict = {}
    parent: str | None = None
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, _, value = line.strip().partition(":")
        value = value.strip().strip('"').strip("'")
        if line.startswith((" ", "\t")) and parent:
            meta[parent][key] = value
        elif value:
            meta[key], parent = value, None
        else:
            meta[key], parent = {}, key
    return meta, match.group(2)


def guidance_for(condition: str) -> str | None:
    if condition == "A":
        return None
    if condition == "B":
        return SHORT_RULE_FILE.read_text(encoding="utf-8").strip()
    if not SKILL_MD.exists():
        sys.exit(f"condition C needs {SKILL_MD.relative_to(ROOT)}")
    return split_frontmatter(SKILL_MD.read_text(encoding="utf-8"))[1].strip()


def build_prompt(case: dict, condition: str) -> str:
    parts = []
    guidance = guidance_for(condition)
    if guidance:
        parts.append(
            "Apply the following writing guidance to this task.\n\n"
            f"<writing-guidance>\n{guidance}\n</writing-guidance>"
        )
    parts.append(f"{case['prompt']}\n\n{NO_REPO_NOTE}")
    for name, text in case_files(case):
        parts.append(f'<file name="{name}">\n{text.rstrip()}\n</file>')
    return "\n\n".join(parts) + "\n"


# ------------------------------------------------------------------------ harnesses


@dataclass
class CallResult:
    ok: bool
    text: str = ""
    structured: dict | None = None
    usage: dict = field(default_factory=dict)
    model: str | None = None
    duration_s: float = 0.0
    error: str | None = None


@dataclass
class HarnessOptions:
    claude_model: str | None = None
    claude_effort: str | None = None
    codex_model: str | None = None
    codex_effort: str | None = None
    timeout: int = 600

    def with_effort(self, effort: str | None) -> "HarnessOptions":
        if not effort:
            return self
        return dataclasses.replace(self, claude_effort=effort, codex_effort=effort)


def call_claude(prompt: str, opts: HarnessOptions, schema: dict | None = None) -> CallResult:
    cmd = ["claude", "-p", "--output-format", "json", "--tools", "",
           "--disable-slash-commands", "--no-session-persistence"]
    if opts.claude_model:
        cmd += ["--model", opts.claude_model]
    if opts.claude_effort:
        cmd += ["--effort", opts.claude_effort]
    if schema:
        cmd += ["--json-schema", json.dumps(schema)]
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="tw-eval-") as cwd:
        try:
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                  cwd=cwd, timeout=opts.timeout)
        except subprocess.TimeoutExpired:
            return CallResult(False, error="timeout", duration_s=time.monotonic() - start)
    duration = time.monotonic() - start
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return CallResult(False, error=f"exit {proc.returncode}: {(proc.stderr or proc.stdout)[-500:]}",
                          duration_s=duration)
    usage = data.get("usage") or {}
    return CallResult(
        ok=not data.get("is_error") and proc.returncode == 0,
        text=data.get("result") or "",
        structured=data.get("structured_output"),
        usage={
            "input_tokens": sum(usage.get(k) or 0 for k in
                                ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")),
            "output_tokens": usage.get("output_tokens") or 0,
            "cost_usd": data.get("total_cost_usd"),
        },
        model=",".join(sorted((data.get("modelUsage") or {}).keys())) or opts.claude_model,
        duration_s=duration,
        error=None if not data.get("is_error") else str(data.get("result"))[:500],
    )


def codex_base_cmd(opts: HarnessOptions, cwd: str) -> list[str]:
    cmd = ["codex", "exec", "--ignore-user-config", "--ephemeral", "-s", "read-only",
           "-C", cwd, "--json"]
    if opts.codex_model:
        cmd += ["-m", opts.codex_model]
    if opts.codex_effort:
        cmd += ["-c", f'model_reasoning_effort="{opts.codex_effort}"']
    return cmd


def call_codex(prompt: str, opts: HarnessOptions, schema: dict | None = None) -> CallResult:
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="tw-eval-") as cwd:
        out_file = Path(cwd) / ".last-message"
        cmd = codex_base_cmd(opts, cwd) + ["--skip-git-repo-check", "-o", str(out_file)]
        if schema:
            schema_file = Path(cwd) / ".schema.json"
            schema_file.write_text(json.dumps(schema))
            cmd += ["--output-schema", str(schema_file)]
        try:
            proc = subprocess.run(cmd + ["-"], input=prompt, capture_output=True, text=True,
                                  cwd=cwd, timeout=opts.timeout)
        except subprocess.TimeoutExpired:
            return CallResult(False, error="timeout", duration_s=time.monotonic() - start)
        text = out_file.read_text(encoding="utf-8") if out_file.exists() else ""
    usage = {"input_tokens": 0, "output_tokens": 0}
    errors = []
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "turn.completed":
            for key in usage:
                usage[key] += (event.get("usage") or {}).get(key) or 0
        elif event.get("type") in ("turn.failed", "error"):
            errors.append(json.dumps(event)[:500])
    structured = None
    if schema and text:
        try:
            structured = json.loads(text)
        except json.JSONDecodeError:
            errors.append("final message is not valid JSON")
    ok = proc.returncode == 0 and bool(text) and not errors
    return CallResult(ok, text=text, structured=structured, usage=usage, model=opts.codex_model,
                      duration_s=time.monotonic() - start,
                      error="; ".join(errors) or (None if ok else f"exit {proc.returncode}: {proc.stderr[-500:]}"))


def call(harness: str, prompt: str, opts: HarnessOptions, schema: dict | None = None) -> CallResult:
    return (call_claude if harness == "claude" else call_codex)(prompt, opts, schema)


def other_harness(harness: str) -> str:
    return "codex" if harness == "claude" else "claude"


def parallel(tasks: list[Callable[[], str]], jobs: int) -> None:
    total = len(tasks)
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = [pool.submit(task) for task in tasks]
        for done, future in enumerate(as_completed(futures), 1):
            print(f"[{done}/{total}] {future.result()}", flush=True)


# ------------------------------------------------------------------------ generate


def gen_dir(run: Path, harness: str, case: dict, condition: str, trial: int) -> Path:
    return run / "gen" / harness / case["name"] / condition / f"t{trial}"


def load_meta(path: Path) -> dict | None:
    meta = path / "meta.json"
    return load_json(meta) if meta.exists() else None


def cmd_generate(args, opts: HarnessOptions) -> None:
    run = run_dir(args.run_id)
    write_manifest(run, args, opts)
    tasks = []
    for harness in args.harness:
        for case in select_cases(args.cases):
            for condition in args.conditions:
                prompt = build_prompt(case, condition)
                for trial in range(1, args.trials + 1):
                    out = gen_dir(run, harness, case, condition, trial)
                    meta = load_meta(out)
                    if meta and meta.get("ok"):
                        continue
                    tasks.append(lambda h=harness, c=case, k=condition, t=trial, p=prompt, o=out:
                                 generate_one(h, c, k, t, p, o, opts))
    print(f"generate: {len(tasks)} unit(s) to run in {run.relative_to(ROOT)}")
    parallel(tasks, args.jobs)


def generate_one(harness, case, condition, trial, prompt, out: Path, opts) -> str:
    result = call(harness, prompt, opts)
    out.mkdir(parents=True, exist_ok=True)
    (out / "output.md").write_text(result.text, encoding="utf-8")
    meta = {"ok": result.ok, "harness": harness, "case": case["name"], "condition": condition,
            "trial": trial, "prompt_sha": sha256(prompt), "model": result.model,
            "usage": result.usage, "duration_s": round(result.duration_s, 1),
            "output_words": word_count(result.text), "error": result.error}
    (out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    status = "ok" if result.ok else f"FAILED ({result.error})"
    return f"{harness} {case['name']} {condition} t{trial} {status} {result.duration_s:.0f}s"


# --------------------------------------------------------------------------- check


def first_code_block(text: str) -> str | None:
    match = re.search(r"```[^\n]*\n(.*?)\n```", text, re.S)
    return match.group(1) if match else None


def check_target(text: str, target: str) -> str:
    if target == "output":
        return text
    message = (first_code_block(text) or text).strip()
    if target == "message":
        return message
    return next((line for line in message.splitlines() if line.strip()), "")


def regex_flags(spec: str) -> int:
    return sum({"i": re.I, "m": re.M, "s": re.S}[c] for c in spec)


def run_check(check: dict, text: str, input_words: int) -> tuple[bool, str]:
    target = check_target(text, check.get("target", "output"))
    if check.get("ignore_quoted"):
        # A quoted phrase is a mention (e.g. a note naming what was removed), not a use.
        target = re.sub(r'"[^"\n]*"|“[^”\n]*”', '""', target)
    kind = check["type"]
    if kind in ("contains", "not_contains"):
        value = check["value"]
        found = (value.lower() in target.lower()) if "i" in check.get("flags", "") else (value in target)
        return (found, "") if kind == "contains" else (not found, value if found else "")
    if kind in ("regex", "not_regex"):
        match = re.search(check["pattern"], target, regex_flags(check.get("flags", "")))
        if kind == "regex":
            return bool(match), ""
        return match is None, match.group(0) if match else ""
    ratio = word_count(text) / max(1, input_words)
    return ratio <= check["value"], f"ratio {ratio:.2f}"


def cmd_check(args) -> None:
    run = run_dir(args.run_id, must_exist=True)
    rows = []
    for case in load_cases():
        input_words = sum(word_count(text) for _, text in case_files(case))
        for meta_file in sorted((run / "gen").glob(f"*/{case['name']}/*/t*/meta.json")):
            meta = load_json(meta_file)
            if not meta.get("ok"):
                continue
            text = (meta_file.parent / "output.md").read_text(encoding="utf-8")
            for check in case["checks"]:
                passed, detail = run_check(check, text, input_words)
                rows.append({"harness": meta["harness"], "case": case["name"],
                             "condition": meta["condition"], "trial": meta["trial"],
                             "check": check["id"], "passed": passed, "detail": detail})
    (run / "checks.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    failed = sum(not r["passed"] for r in rows)
    print(f"check: {len(rows)} results, {failed} failed -> {(run / 'checks.json').relative_to(ROOT)}")


def cmd_selftest(_args) -> int:
    """Grader self-test: each reference solution passes every check, and each raw
    fixture fails at least one, so the checks are satisfiable and discriminating."""
    errors = 0
    for case in load_cases():
        input_words = sum(word_count(text) for _, text in case_files(case))
        reference = EVALS / "reference" / f"{case['name']}.md"
        if not reference.exists():
            print(f"FAIL {case['name']}: no reference solution at {reference.relative_to(ROOT)}")
            errors += 1
            continue
        ref_text = reference.read_text(encoding="utf-8")
        ref_failures = [c["id"] for c in case["checks"] if not run_check(c, ref_text, input_words)[0]]
        raw_text = "\n\n".join(text for _, text in case_files(case))
        raw_failures = [c["id"] for c in case["checks"] if not run_check(c, raw_text, input_words)[0]]
        ok = not ref_failures and raw_failures
        errors += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {case['name']}: reference fails {ref_failures or 'none'}; "
              f"raw input fails {len(raw_failures)}/{len(case['checks'])} checks")
    print("selftest: ok" if not errors else f"selftest: {errors} case(s) failed")
    return 1 if errors else 0


# --------------------------------------------------------------------------- judge


def judge_schema() -> dict:
    def obj(props: dict) -> dict:
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


def build_judge_prompt(case: dict, outputs: dict[str, str]) -> str:
    files = "\n\n".join(f'<file name="{n}">\n{t.rstrip()}\n</file>' for n, t in case_files(case))
    expectations = "\n".join(f"{i}. {e}" for i, e in enumerate(case["expectations"], 1))
    responses = "\n\n".join(f'<response label="{label}">\n{text.rstrip()}\n</response>'
                            for label, text in outputs.items())
    return (JUDGE_TEMPLATE.read_text(encoding="utf-8")
            .replace("{{TASK}}", case["prompt"])
            .replace("{{FILES}}", files)
            .replace("{{EXPECTATIONS}}", expectations)
            .replace("{{RESPONSES}}", responses))


def validate_judgement(data: dict | None, labels: list[str], n_expectations: int) -> str | None:
    if not isinstance(data, dict):
        return "no structured output"
    if sorted(r.get("label") for r in data.get("responses", [])) != sorted(labels):
        return "response labels do not match"
    for response in data["responses"]:
        if sorted(e["index"] for e in response["expectations"]) != list(range(1, n_expectations + 1)):
            return f"expectation indexes incomplete for {response['label']}"
    if sorted(d["dimension"] for d in data.get("dimensions", [])) != sorted(DIMENSIONS):
        return "dimensions incomplete"
    for dim in data["dimensions"]:
        if sorted(r["label"] for r in dim["ranks"]) != sorted(labels):
            return f"ranks incomplete for {dim['dimension']}"
    return None


def cmd_judge(args, opts: HarnessOptions) -> None:
    run = run_dir(args.run_id, must_exist=True)
    tasks = []
    for harness in args.harness:
        for case in select_cases(args.cases):
            trials = sorted({p.name for p in (run / "gen" / harness / case["name"]).glob("*/t*")})
            for trial_name in trials:
                out = run / "judge" / harness / case["name"] / f"{trial_name}.json"
                if out.exists():
                    continue
                outputs = {}
                for condition in CONDITIONS:
                    meta = load_meta(gen_dir(run, harness, case, condition, int(trial_name[1:])))
                    if meta and meta.get("ok"):
                        outputs[condition] = (gen_dir(run, harness, case, condition, int(trial_name[1:]))
                                              / "output.md").read_text(encoding="utf-8")
                if len(outputs) < 2:
                    continue
                judge = args.judge_harness if args.judge_harness != "cross" else other_harness(harness)
                tasks.append(lambda h=harness, c=case, t=trial_name, o=outputs, j=judge, f=out:
                             judge_one(args.run_id, h, c, t, o, j, f, opts.with_effort(args.judge_effort)))
    print(f"judge: {len(tasks)} group(s) to grade")
    parallel(tasks, args.jobs)


def judge_one(run_id, harness, case, trial_name, outputs: dict[str, str], judge, out: Path, opts) -> str:
    conditions = list(outputs)
    random.Random(f"{run_id}:{harness}:{case['name']}:{trial_name}").shuffle(conditions)
    mapping = {f"R{i}": cond for i, cond in enumerate(conditions, 1)}
    prompt = build_judge_prompt(case, {label: outputs[cond] for label, cond in mapping.items()})
    error = None
    for _attempt in range(2):
        result = call(judge, prompt, opts, schema=judge_schema())
        error = result.error or validate_judgement(result.structured, list(mapping), len(case["expectations"]))
        if result.ok and not error:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({"judge": judge, "judge_model": result.model, "mapping": mapping,
                                       "usage": result.usage, "result": result.structured}, indent=2),
                           encoding="utf-8")
            return f"{harness} {case['name']} {trial_name} judged by {judge} ok"
    return f"{harness} {case['name']} {trial_name} judged by {judge} FAILED ({error})"


# ------------------------------------------------------------------------ triggers


def stream_until(cmd: list[str], cwd: str, timeout: int, detect: Callable[[dict], bool],
                 on_event: Callable[[dict], None] | None = None) -> tuple[bool, str]:
    """Run cmd, parse JSONL events, stop early on detection. Returns (detected, how it ended)."""
    proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True)
    timer = threading.Timer(timeout, proc.kill)
    timer.start()
    try:
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if on_event:
                on_event(event)
            if detect(event):
                proc.kill()
                return True, "detected"
        proc.wait()
        return False, "timeout" if not timer.is_alive() else "finished"
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
        proc.wait()


def claude_trigger(query: str, project: str, opts: HarnessOptions) -> dict:
    cmd = ["claude", "-p", query, "--output-format", "stream-json", "--verbose", "--max-turns", "3",
           "--no-session-persistence", "--tools", "Skill,Read,Glob,Grep"]
    if opts.claude_model:
        cmd += ["--model", opts.claude_model]
    seen = {"discovered": None}

    def on_event(event: dict) -> None:
        if event.get("type") == "system" and event.get("subtype") == "init":
            seen["discovered"] = SKILL_NAME in (event.get("skills") or [])

    def detect(event: dict) -> bool:
        if event.get("type") != "assistant":
            return False
        for block in event.get("message", {}).get("content", []):
            if block.get("type") != "tool_use":
                continue
            payload = json.dumps(block.get("input", {}))
            if block.get("name") == "Skill" and SKILL_NAME in payload:
                return True
            if block.get("name") == "Read" and f"{SKILL_NAME}/SKILL.md" in payload:
                return True
        return False

    triggered, ended = stream_until(cmd, project, opts.timeout, detect, on_event)
    return {"triggered": triggered, "ended": ended, "discovered": seen["discovered"]}


def codex_trigger(query: str, project: str, opts: HarnessOptions) -> dict:
    cmd = codex_base_cmd(opts, project) + [query]

    def detect(event: dict) -> bool:
        item = event.get("item") or {}
        return item.get("type") not in (None, "agent_message", "reasoning") and \
            f"{SKILL_NAME}/SKILL.md" in json.dumps(item)

    triggered, ended = stream_until(cmd, project, opts.timeout, detect)
    return {"triggered": triggered, "ended": ended, "discovered": None}


def trigger_one(harness: str, index: int, attempt: int, item: dict, out: Path, opts) -> str:
    with tempfile.TemporaryDirectory(prefix="tw-trigger-") as project:
        skills_dir = Path(project) / (".claude/skills" if harness == "claude" else ".agents/skills")
        skills_dir.mkdir(parents=True)
        (skills_dir / SKILL_NAME).symlink_to(SKILL_DIR, target_is_directory=True)
        subprocess.run(["git", "init", "-q", project], check=True)
        start = time.monotonic()
        result = (claude_trigger if harness == "claude" else codex_trigger)(item["query"], project, opts)
    result.update({"harness": harness, "index": index, "attempt": attempt, "query": item["query"],
                   "should_trigger": item["should_trigger"], "duration_s": round(time.monotonic() - start, 1)})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    mark = "hit" if result["triggered"] == item["should_trigger"] else "MISS"
    return f"{harness} q{index} r{attempt} triggered={result['triggered']} {mark} ({result['ended']})"


def cmd_triggers(args, opts: HarnessOptions) -> None:
    if not SKILL_MD.exists():
        sys.exit(f"triggers need {SKILL_MD.relative_to(ROOT)}")
    run = run_dir(args.run_id)
    write_manifest(run, args, opts)
    trigger_opts = dataclasses.replace(opts, timeout=args.trigger_timeout)
    tasks = []
    for harness in args.harness:
        for index, item in enumerate(load_json(TRIGGERS_FILE), 1):
            for attempt in range(1, args.trigger_runs + 1):
                out = run / "triggers" / harness / f"q{index:02d}-r{attempt}.json"
                if not out.exists():
                    tasks.append(lambda h=harness, i=index, a=attempt, it=item, o=out:
                                 trigger_one(h, i, a, it, o, trigger_opts))
    print(f"triggers: {len(tasks)} run(s)")
    parallel(tasks, args.jobs)


# -------------------------------------------------------------------------- report


def mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def stdev(values: list[float]) -> float | None:
    return statistics.stdev(values) if len(values) > 1 else None


def fmt(value: float | None, pct: bool = True) -> str:
    if value is None:
        return "–"
    return f"{value * 100:.0f}%" if pct else f"{value:,.0f}"


def fmt_mean_sd(values: list[float]) -> str:
    m, s = mean(values), stdev(values)
    return fmt(m) if s is None else f"{fmt(m)} ± {s * 100:.0f}"


def pairwise(judgements: list[dict], first: str, second: str) -> dict:
    """Win/tie/loss counts of condition `first` against `second`, per dimension and per case."""
    by_dim = {d: [0, 0, 0] for d in DIMENSIONS}
    by_case: dict[str, int] = {}
    for j in judgements:
        label_of = {cond: label for label, cond in j["mapping"].items()}
        if first not in label_of or second not in label_of:
            continue
        for dim in j["result"]["dimensions"]:
            ranks = {r["label"]: r["rank"] for r in dim["ranks"]}
            a, b = ranks[label_of[first]], ranks[label_of[second]]
            outcome = 0 if a < b else 1 if a == b else 2
            by_dim[dim["dimension"]][outcome] += 1
            by_case[j["case"]] = by_case.get(j["case"], 0) + (1 if a < b else -1 if a > b else 0)
    return {"by_dimension": by_dim, "net_by_case": by_case}


def cmd_report(args) -> None:
    run = run_dir(args.run_id, must_exist=True)
    cases = load_cases()
    metas = [load_json(p) for p in sorted((run / "gen").glob("*/*/*/t*/meta.json"))]
    checks = load_json(run / "checks.json") if (run / "checks.json").exists() else []
    judgements = []
    for path in sorted((run / "judge").glob("*/*/t*.json")):
        data = load_json(path)
        data.update({"harness": path.parts[-3], "case": path.parts[-2], "trial": path.stem})
        judgements.append(data)
    triggers = [load_json(p) for p in sorted((run / "triggers").glob("*/q*.json"))]
    harnesses = sorted({m["harness"] for m in metas} | {t["harness"] for t in triggers})
    conditions = [c for c in CONDITIONS if any(m["condition"] == c for m in metas)]

    summary: dict = {"run_id": args.run_id, "harnesses": {}, "gates": {}}
    lines = [f"# Eval report: {args.run_id}", "", f"Manifest: `{(run / 'manifest.json').relative_to(ROOT)}`", ""]

    for harness in harnesses:
        h_metas = [m for m in metas if m["harness"] == harness]
        h_checks = [c for c in checks if c["harness"] == harness]
        h_judge = [j for j in judgements if j["harness"] == harness]
        h_sum: dict = {"conditions": {}}
        summary["harnesses"][harness] = h_sum
        lines += [f"## {harness}", ""]
        if h_metas:
            models = sorted({m.get("model") or "default" for m in h_metas})
            failed = [m for m in h_metas if not m["ok"]]
            judges = sorted({f"{j['judge']} ({j.get('judge_model') or 'default'})" for j in h_judge})
            lines += [f"Writer model: {', '.join(models)}. Judge: {', '.join(judges) or 'none'}. "
                      f"Failed generations: {len(failed)}.", ""]
            lines += ["| Condition | Checks passed (mean ± sd over trials) | Expectations passed (judge) "
                      "| Fidelity passed (judge) | Sensitive leaks | Input tokens | Output words |",
                      "|---|---|---|---|---|---|---|"]
        for cond in conditions:
            c_checks = [c for c in h_checks if c["condition"] == cond]
            per_trial = []
            for trial in sorted({c["trial"] for c in c_checks}):
                rows = [c for c in c_checks if c["trial"] == trial]
                per_trial.append(sum(r["passed"] for r in rows) / len(rows))
            exp_verdicts, fidelity = [], []
            for j in h_judge:
                label = next((l for l, c in j["mapping"].items() if c == cond), None)
                response = next((r for r in j["result"]["responses"] if r["label"] == label), None)
                if response:
                    exp_verdicts += [e["verdict"] for e in response["expectations"]]
                    fidelity.append(response["fidelity"] == "pass")
            known = [v for v in exp_verdicts if v != "unknown"]
            c_metas = [m for m in h_metas if m["condition"] == cond and m["ok"]]
            leaks = sum(1 for c in c_checks if c["check"] in SENSITIVE_CHECKS and not c["passed"])
            row = {
                "check_pass_rate": mean(per_trial), "check_pass_sd": stdev(per_trial),
                "expectation_pass_rate": (sum(v == "pass" for v in known) / len(known)) if known else None,
                "expectation_unknown": exp_verdicts.count("unknown"),
                "fidelity_pass_rate": mean([float(f) for f in fidelity]),
                "sensitive_leaks": leaks,
                "mean_input_tokens": mean([m["usage"].get("input_tokens", 0) for m in c_metas]),
                "mean_output_words": mean([m["output_words"] for m in c_metas]),
            }
            h_sum["conditions"][cond] = row
            lines.append(f"| {cond} ({CONDITIONS[cond]}) | {fmt_mean_sd(per_trial)} "
                         f"| {fmt(row['expectation_pass_rate'])} | {fmt(row['fidelity_pass_rate'])} "
                         f"| {leaks} | {fmt(row['mean_input_tokens'], False)} "
                         f"| {fmt(row['mean_output_words'], False)} |")
        if h_metas:
            lines.append("")
            base = h_sum["conditions"].get("A", {}).get("mean_input_tokens")
            for cond in ("B", "C"):
                tokens = h_sum["conditions"].get(cond, {}).get("mean_input_tokens")
                if base and tokens:
                    h_sum["conditions"][cond]["guidance_overhead_tokens"] = tokens - base
                    lines.append(f"Measured guidance overhead, {cond} vs A: {tokens - base:,.0f} input tokens per call.")
            lines.append("")

        # Per-case deterministic results.
        if h_checks:
            lines += ["| Case | " + " | ".join(f"{c} checks" for c in conditions) + " | Failing checks under C |",
                      "|---|" + "---|" * len(conditions) + "---|"]
            for case in cases:
                cells = []
                for cond in conditions:
                    rows = [c for c in h_checks if c["case"] == case["name"] and c["condition"] == cond]
                    cells.append(fmt(sum(r["passed"] for r in rows) / len(rows)) if rows else "–")
                failing = sorted({c["check"] for c in h_checks if c["case"] == case["name"]
                                  and c["condition"] == "C" and not c["passed"]})
                lines.append(f"| {case['name']} | " + " | ".join(cells) + f" | {', '.join(failing) or '–'} |")
            lines.append("")

        # Pairwise preferences from the judge.
        for first, second in (("C", "B"), ("C", "A"), ("B", "A")):
            pw = pairwise(h_judge, first, second)
            if not any(sum(v) for v in pw["by_dimension"].values()):
                continue
            h_sum[f"{first}_vs_{second}"] = pw
            lines += [f"Judge preference, {first} vs {second} (win / tie / loss over cases × trials):", ""]
            lines += ["| Dimension | Win | Tie | Loss |", "|---|---|---|---|"]
            for dim, (w, t, l) in pw["by_dimension"].items():
                lines.append(f"| {dim} | {w} | {t} | {l} |")
            net = pw["net_by_case"]
            lines += ["", "Net preference by case (wins minus losses across dimensions and trials): "
                      + ", ".join(f"{k} {v:+d}" for k, v in sorted(net.items())), ""]

        # Position bias audit: how often the response shown first was ranked best.
        firsts = [min(r["rank"] for r in d["ranks"]) == next(r["rank"] for r in d["ranks"] if r["label"] == "R1")
                  for j in h_judge for d in j["result"]["dimensions"]]
        if firsts:
            n = len(h_judge[0]["mapping"])
            h_sum["position_bias_r1_best"] = mean([float(f) for f in firsts])
            lines += [f"Position audit: the first-shown response was ranked best (ties included) in "
                      f"{fmt(h_sum['position_bias_r1_best'])} of dimension judgements; "
                      f"about {fmt(1 / n)} or more is expected without bias, since ties count.", ""]

        # Trigger results.
        h_trig = [t for t in triggers if t["harness"] == harness]
        if h_trig:
            pos = [t["triggered"] for t in h_trig if t["should_trigger"]]
            neg = [t["triggered"] for t in h_trig if not t["should_trigger"]]
            recall, fpr = mean([float(x) for x in pos]), mean([float(x) for x in neg])
            undiscovered = sum(1 for t in h_trig if t.get("discovered") is False)
            h_sum["triggers"] = {"recall": recall, "false_positive_rate": fpr, "runs": len(h_trig),
                                 "undiscovered_runs": undiscovered}
            lines += [f"Triggering: recall {fmt(recall)} on should-trigger queries, false-positive rate "
                      f"{fmt(fpr)} on near misses ({len(h_trig)} runs"
                      + (f"; skill not discovered in {undiscovered} runs" if undiscovered else "") + ").", ""]
            lines += ["| # | Expected | Trigger rate | Query |", "|---|---|---|---|"]
            for index in sorted({t["index"] for t in h_trig}):
                runs = [t for t in h_trig if t["index"] == index]
                rate = mean([float(t["triggered"]) for t in runs])
                miss = "" if (rate >= 0.5) == runs[0]["should_trigger"] else " ⚠"
                query = runs[0]["query"][:90] + ("…" if len(runs[0]["query"]) > 90 else "")
                lines.append(f"| {index} | {'yes' if runs[0]['should_trigger'] else 'no'} | {fmt(rate)}{miss} | {query} |")
            lines.append("")

    lines += gates(summary, cases)
    (run / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (run / "report.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\n".join(lines))


def gates(summary: dict, cases: list[dict]) -> list[str]:
    """Apply the pre-registered release gates from evals/README.md."""
    results: dict[str, bool | None] = {}
    notes = []
    for harness, h in summary["harnesses"].items():
        b, c = h["conditions"].get("B"), h["conditions"].get("C")
        if b and c:
            results[f"{harness}: no sensitive leaks under C"] = c["sensitive_leaks"] == 0
            results[f"{harness}: C checks ≥ B checks − {FIDELITY_TOLERANCE:.0%}"] = (
                c["check_pass_rate"] >= b["check_pass_rate"] - FIDELITY_TOLERANCE)
            if c["fidelity_pass_rate"] is not None and b["fidelity_pass_rate"] is not None:
                results[f"{harness}: C judge fidelity ≥ B − {FIDELITY_TOLERANCE:.0%}"] = (
                    c["fidelity_pass_rate"] >= b["fidelity_pass_rate"] - FIDELITY_TOLERANCE)
        pw = h.get("C_vs_B")
        if pw:
            wins = sum(v[0] for v in pw["by_dimension"].values())
            losses = sum(v[2] for v in pw["by_dimension"].values())
            preferred = sum(1 for v in pw["net_by_case"].values() if v > 0)
            needed = math.ceil(len(cases) / 2)
            results[f"{harness}: judge prefers C over B overall"] = wins > losses
            results[f"{harness}: C preferred over B on ≥ {needed}/{len(cases)} cases"] = preferred >= needed
            notes.append(f"{harness}: C vs B {wins} wins / {losses} losses; preferred on {preferred} case(s).")
        t = h.get("triggers")
        if t:
            results[f"{harness}: trigger recall ≥ {MIN_TRIGGER_RECALL:.0%}"] = t["recall"] >= MIN_TRIGGER_RECALL
            results[f"{harness}: trigger false positives ≤ {MAX_TRIGGER_FALSE_POSITIVE:.0%}"] = (
                t["false_positive_rate"] <= MAX_TRIGGER_FALSE_POSITIVE)
    summary["gates"] = results
    lines = ["## Release gates", ""] + [f"- {'PASS' if ok else 'FAIL'}: {name}" for name, ok in results.items()]
    lines += [""] + notes
    safety = all(ok for name, ok in results.items() if "leak" in name or "≥ B" in name)
    value = all(ok for name, ok in results.items() if "over B" in name)
    trig = all(ok for name, ok in results.items() if "trigger" in name)
    if not results:
        verdict = "INCOMPLETE: no gate could be evaluated."
    elif not safety:
        verdict = "FIX: the skill loses fidelity or safety relative to the short rule."
    elif not value:
        verdict = "SIMPLIFY: the short rule performs about as well; prefer it over the skill."
    elif not trig:
        verdict = "FIX DESCRIPTION: content passes but triggering misses the thresholds."
    else:
        verdict = "SHIP: every evaluated gate passes."
    summary["verdict"] = verdict
    return lines + ["", f"**Verdict:** {verdict}", ""]


# ---------------------------------------------------------------------------- lint


def cmd_lint(_args) -> int:
    errors, warnings = [], []
    if SKILL_MD.exists():
        meta, body = split_frontmatter(SKILL_MD.read_text(encoding="utf-8"))
        name, desc = meta.get("name", ""), meta.get("description", "")
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name) or len(name) > 64:
            errors.append(f"name {name!r} violates the Agent Skills name rules")
        if name != SKILL_DIR.name:
            errors.append(f"name {name!r} does not match directory {SKILL_DIR.name!r}")
        if not 1 <= len(desc) <= 1024:
            errors.append(f"description length {len(desc)} is outside 1–1024")
        elif len(desc) > DESCRIPTION_BUDGET_CHARS:
            errors.append(f"description is {len(desc)} chars; budget is {DESCRIPTION_BUDGET_CHARS}")
        if re.search(r"[<>]", desc):
            errors.append("description must not contain XML tags")
        if re.match(r"(I|You|We)\b", desc):
            warnings.append("description should be third person")
        unknown = set(meta) - {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
        if unknown:
            errors.append(f"frontmatter keys outside the Agent Skills spec: {sorted(unknown)}")
        lines = body.count("\n") + 1
        tokens = len(body) // 4
        if lines > 500:
            errors.append(f"body has {lines} lines; the spec recommends under 500")
        if tokens > BODY_BUDGET_TOKENS:
            errors.append(f"body is ~{tokens} tokens; budget is {BODY_BUDGET_TOKENS}")
        for target in re.findall(r"\]\(([^)#\s]+)\)", body):
            if not re.match(r"[a-z]+://", target) and not (SKILL_DIR / target).exists():
                errors.append(f"broken relative link: {target}")
        print(f"skill: description {len(desc)} chars (~{len(desc) // 4} tokens); body {lines} lines (~{tokens} tokens)")
        yaml = SKILL_DIR / "agents" / "openai.yaml"
        if yaml.exists():
            short = re.search(r'short_description:\s*"([^"]*)"', yaml.read_text(encoding="utf-8"))
            if short and not 25 <= len(short.group(1)) <= 64:
                errors.append(f"openai.yaml short_description is {len(short.group(1))} chars; Codex wants 25–64")
    else:
        warnings.append(f"{SKILL_MD.relative_to(ROOT)} does not exist yet; condition C and triggers will not run")

    cases = load_cases()
    seen_ids, seen_names = set(), set()
    for case in cases:
        where = f"case {case.get('name', case.get('id'))}"
        for key in ("id", "name", "prompt", "files", "expectations", "checks"):
            if key not in case:
                errors.append(f"{where}: missing {key}")
        if case["id"] in seen_ids or case["name"] in seen_names:
            errors.append(f"{where}: duplicate id or name")
        seen_ids.add(case["id"])
        seen_names.add(case["name"])
        for f in case["files"]:
            if not (ROOT / f).exists():
                errors.append(f"{where}: missing file {f}")
        check_ids = [c["id"] for c in case["checks"]]
        if len(set(check_ids)) != len(check_ids):
            errors.append(f"{where}: duplicate check ids")
        for check in case["checks"]:
            if check["type"] not in CHECK_TYPES:
                errors.append(f"{where}: {check['id']} has unknown type {check['type']}")
            if check.get("target", "output") not in CHECK_TARGETS:
                errors.append(f"{where}: {check['id']} has unknown target")
            if "pattern" in check:
                try:
                    re.compile(check["pattern"], regex_flags(check.get("flags", "")))
                except re.error as exc:
                    errors.append(f"{where}: {check['id']} pattern does not compile: {exc}")
    triggers = load_json(TRIGGERS_FILE)
    positives = sum(1 for t in triggers if t["should_trigger"])
    if any(set(t) - {"query", "should_trigger", "note"} or "query" not in t for t in triggers):
        errors.append("triggers.json items need query and should_trigger only (note is optional)")
    if abs(positives - (len(triggers) - positives)) > 2:
        warnings.append(f"triggers.json is unbalanced: {positives} positive, {len(triggers) - positives} negative")
    print(f"data: {len(cases)} cases, {sum(len(c['checks']) for c in cases)} checks, "
          f"{sum(len(c['expectations']) for c in cases)} expectations, {len(triggers)} trigger queries")
    for w in warnings:
        print(f"warning: {w}")
    for e in errors:
        print(f"error: {e}")
    print("lint: ok" if not errors else f"lint: {len(errors)} error(s)")
    return 1 if errors else 0


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


def write_manifest(run: Path, args, opts: HarnessOptions) -> None:
    path = run / "manifest.json"
    manifest = load_json(path) if path.exists() else {"created": dt.datetime.now(dt.timezone.utc).isoformat()}
    manifest.setdefault("invocations", []).append({
        "at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "argv": sys.argv[1:],
        "git_commit": tool_version(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"]),
        "git_dirty": bool(tool_version(["git", "-C", str(ROOT), "status", "--porcelain"])),
        "skill_sha": sha256(SKILL_MD.read_text(encoding="utf-8")) if SKILL_MD.exists() else None,
        "short_rule_sha": sha256(SHORT_RULE_FILE.read_text(encoding="utf-8")),
        "cases_sha": sha256(CASES_FILE.read_text(encoding="utf-8")),
        "claude_version": tool_version(["claude", "--version"]) if shutil.which("claude") else None,
        "codex_version": tool_version(["codex", "--version"]) if shutil.which("codex") else None,
        "options": vars(opts),
    })
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


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
        p.add_argument("--judge-harness", default="cross", choices=["cross", *HARNESSES])
        p.add_argument("--trigger-runs", type=int, default=3)
        p.add_argument("--trigger-timeout", type=int, default=180)
        p.add_argument("--timeout", type=int, default=600, help="seconds per generation or judge call")
        p.add_argument("--claude-model")
        p.add_argument("--claude-effort")
        p.add_argument("--codex-model")
        p.add_argument("--codex-effort")
        p.add_argument("--judge-effort", help="reasoning effort for judge calls (either harness)")
    args = parser.parse_args(argv)
    for value in getattr(args, "harness", []):
        if value not in HARNESSES:
            parser.error(f"unknown harness {value}")
    for value in getattr(args, "conditions", []):
        if value not in CONDITIONS:
            parser.error(f"unknown condition {value}")
    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.command == "lint":
        return cmd_lint(args)
    if args.command == "selftest":
        return cmd_selftest(args)
    opts = HarnessOptions(args.claude_model, args.claude_effort, args.codex_model, args.codex_effort, args.timeout)
    steps = {
        "generate": lambda: cmd_generate(args, opts),
        "check": lambda: cmd_check(args),
        "judge": lambda: cmd_judge(args, opts),
        "triggers": lambda: cmd_triggers(args, opts),
        "report": lambda: cmd_report(args),
    }
    for step in (steps if args.command == "all" else [args.command]):
        steps[step]()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
