"""The claude and codex command lines, and what their output means: replies, token usage,
errors, and whether a trigger run loaded the skill. Also which CLIs judge whose outputs."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from pathlib import Path

from pydantic import ValidationError

from .models import (
    HARNESSES, SKILL_NAME, CallResult, ClaudeEvent, ClaudeReply, ClaudeUsage, CodexEvent, HarnessOptions, parsed,
)


def json_values(lines: Iterable[str]) -> Iterator[object]:
    """The lines of a JSONL stream that parse as JSON; other lines are skipped."""
    for line in lines:
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


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


def claude_trigger_command(query: str, opts: HarnessOptions) -> list[str]:
    return ["claude", "-p", query, "--output-format", "stream-json", "--verbose", "--max-turns", "3",
            "--no-session-persistence", "--tools", "Skill,Read,Glob,Grep", *flag("--model", opts.claude_model)]


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


OTHER_FAMILY = {"claude": "codex", "codex": "claude"}

JUDGE_MODES: Mapping[str, Callable[[str], list[str]]] = {
    # Both families judge every group, which separates writer effects from judge taste.
    "panel": lambda harness: list(HARNESSES),
    # Only the other family.
    "cross": lambda harness: [OTHER_FAMILY[harness]],
}


def judges_for(harness: str, mode: str) -> list[str]:
    """The judges for one writer's groups: a mode from JUDGE_MODES, or one named harness."""
    judges = JUDGE_MODES.get(mode)
    return judges(harness) if judges else [mode]
