"""Fake `claude` and `codex` CLIs for capture.sh. No model is called.

Usage: fake.py claude|codex ARGS... (capture.sh installs wrappers named `claude` and `codex`).
Every call is logged to $FAKE_STATE/log (argv, cwd, stdin, the --output-schema file) and counted
per (CLI, argv without temporary paths, stdin), so a reply can fail on the first call and succeed
on a later one. Replies depend only on the input and that count.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

NAME = sys.argv[1]
ARGS = sys.argv[2:]
STATE = Path(os.environ["FAKE_STATE"])
TEMP = re.compile(r"/[^\s\"']*?(tw-(?:eval|trigger)-)[A-Za-z0-9_]+")
DIMENSIONS = ("clarity", "naturalness", "brevity", "conventions")


def h(*parts: object) -> int:
    return int(hashlib.sha256("\x00".join(map(str, parts)).encode()).hexdigest()[:12], 16)


def increment(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE / ".lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        n = int(path.read_text()) + 1 if path.exists() else 1
        path.write_text(str(n))
    return n


def opt(flag: str) -> str | None:
    return ARGS[ARGS.index(flag) + 1] if flag in ARGS else None


def record(stdin: str) -> int:
    """Log this call; return how many times the same call has been made, this one included."""
    STATE.mkdir(parents=True, exist_ok=True)
    schema_file = opt("--output-schema")
    schema = Path(schema_file).read_text() if schema_file else None
    seq = increment(STATE / "seq")
    log = STATE / "log" / f"{seq:05d}-{NAME}.json"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps({"argv": [NAME, *ARGS], "cwd": os.getcwd(), "stdin": stdin,
                               "output_schema": schema}, indent=1, ensure_ascii=False) + "\n")
    key = hashlib.sha256(json.dumps([NAME, TEMP.sub(r"\1", "\x00".join(ARGS)), stdin]).encode()).hexdigest()
    return increment(STATE / "calls" / key[:24])


def event(kind: str, **fields: object) -> None:
    print(json.dumps({"type": kind, **fields}), flush=True)


def writer_text(prompt: str, n: int) -> str:
    """A deterministic 'rewrite': the source files with some lines dropped."""
    hp = h(prompt, n)
    source = "\n\n".join(re.findall(r'<file name="[^"]*">\n(.*?)\n</file>', prompt, re.S))
    body = "\n".join(line for line in source.splitlines() if h(line, hp) % 4).strip()
    if hp % 3 == 0:
        return f"Here is the message:\n\n```text\nfix(api): handle fake input {hp % 97}\n\n{body}\n```\n"
    note = '\nRemoved "in order to" and similar filler.\n' if hp % 5 == 0 else ""
    return f"# Fake rewrite {hp % 1000}\n\n{body}\n{note}"


def judgement(prompt: str, salt: str) -> dict:
    # Finds the expectations by two passages of the suite's judge.md; change them together.
    labels = list(dict.fromkeys(re.findall(r'<response label="(R\d+)">', prompt)))
    start = prompt.index("Each expectation describes part of a good response.")
    section = prompt[start:prompt.index("\n## Fidelity\n\nFor each response, list:", start)]
    n_exp = max(int(i) for i in re.findall(r"^(\d+)\. ", section, re.M))
    hp = h(prompt, salt)
    responses = [{"label": label, "analysis": f"fake analysis of {label}",
                  "expectations": [{"index": i, "verdict": ("pass", "pass", "fail", "unknown", "pass", "fail")[h(label, i, hp) % 6],
                                    "evidence": f"fake evidence {i}"} for i in range(1, n_exp + 1)],
                  "invented_facts": ["a fake invented fact"] if h(label, "inv", hp) % 3 == 0 else [],
                  "omitted_facts": ["a fake omitted fact"] if h(label, "omit", hp) % 4 == 0 else [],
                  "fidelity": "fail" if h(label, "fid", hp) % 3 == 0 else "pass"} for label in labels]
    dimensions = [{"dimension": d, "analysis": f"fake {d} reasoning",
                   "ranks": [{"label": label, "rank": 1 + h(d, label, hp) % len(labels)} for label in labels]}
                  for d in DIMENSIONS]
    return {"responses": responses, "dimensions": dimensions}


def judge_variant(prompt: str, n: int) -> str:
    """retry: invalid on the first call only. fail1/fail2: invalid on both calls of one judge run."""
    kind = h(prompt, "variant") % 6
    if kind == 0 and n == 1:
        return "retry"
    if kind == 1 and n <= 2:
        return f"fail{n}"
    return "valid"


def claude_json() -> int:
    prompt = sys.stdin.read()
    n = record(prompt)
    model = opt("--model") or "claude-fake-default"
    hp = h(prompt)
    usage = {"input_tokens": len(prompt) // 4, "cache_read_input_tokens": 120,
             "cache_creation_input_tokens": hp % 50, "output_tokens": 0}
    models = {model: {"costUSD": 0.01}, **({"claude-fake-haiku": {}} if hp % 4 == 0 else {})}
    if opt("--json-schema"):
        variant = judge_variant(prompt, n)
        result = judgement(prompt, f"claude{n}")
        reply = {"type": "result", "is_error": False, "result": "graded", "structured_output": result}
        if variant == "retry":
            result["dimensions"].pop()
        elif variant == "fail1":
            reply = {"type": "result", "is_error": False, "result": "I could not produce JSON."}
        elif variant == "fail2":
            result["responses"][0]["expectations"][0]["verdict"] = "maybe"
        print(json.dumps({**reply, "usage": {**usage, "output_tokens": 900}, "modelUsage": models,
                          "total_cost_usd": round(len(prompt) / 1e6, 6)}))
        return 0
    if hp % 29 == 0:
        sys.stderr.write("fake claude: overloaded, giving up")
        return 1
    if n == 1 and hp % 29 == 1:
        print(json.dumps({"type": "result", "is_error": True, "result": "API Error: 529 fake overloaded",
                          "usage": usage}))
        return 1
    if n == 1 and hp % 29 == 2:
        print("Error: fake crash before any JSON")
        return 2
    text = writer_text(prompt, n)
    print(json.dumps({"type": "result", "is_error": False, "result": text,
                      "usage": {**usage, "output_tokens": len(text) // 4}, "modelUsage": models,
                      "total_cost_usd": round(len(prompt) / 1e6, 6)}))
    return 0


def claude_stream() -> int:
    query = opt("-p") or ""
    n = record("")
    hq = h(query)
    linked = Path(".claude/skills/technical-writing/SKILL.md").exists()
    skills = ["writing-for-agents", *(["technical-writing"] if linked and hq % 7 else [])]
    print("fake claude: stream starting", flush=True)
    event("system", subtype="init", skills=skills, tools=["Skill", "Read", "Glob", "Grep"],
          model=opt("--model") or "claude-fake-default")
    if hq % 10 == 0:
        time.sleep(60)
    event("assistant", message={"content": [{"type": "text", "text": "Looking at the request."}]})
    event("assistant", message={"content": [{"type": "tool_use", "name": "Read",
                                             "input": {"file_path": ".claude/skills/writing-for-agents/SKILL.md"}}]})
    if "technical-writing" in skills and h(query, n) % 3:
        name, tool_input = (("Skill", {"skill": "technical-writing"}) if h(query, n) % 2
                            else ("Read", {"file_path": ".claude/skills/technical-writing/SKILL.md"}))
        event("assistant", message={"content": [{"type": "tool_use", "name": name, "input": tool_input}]})
    event("result", subtype="success", is_error=False, result="done")
    return 0


def codex_exec() -> int:
    prompt = sys.stdin.read()
    n = record(prompt)
    out = Path(opt("-o") or "")
    hp = h(prompt)
    print("fake codex: reading prompt from stdin", flush=True)
    event("thread.started", thread_id=f"fake-{hp % 10000}")
    event("turn.started")
    if opt("--output-schema"):
        variant = judge_variant(prompt, n)
        result = judgement(prompt, f"codex{n}")
        if variant == "retry":
            result["responses"][0]["expectations"].pop(0)
        elif variant == "fail2":
            result["responses"][0]["label"] = "R9"
        text = "{not json" if variant == "fail1" else json.dumps(result)
    else:
        if hp % 29 == 0:
            event("turn.failed", error={"message": "fake codex: stream disconnected"})
            return 1
        if n == 1 and hp % 29 == 2:
            sys.stderr.write("fake codex panicked")
            return 101
        if n == 1 and hp % 29 == 1:
            event("error", message="fake codex: reconnecting 1/5")
        text = writer_text(prompt, n)
    event("item.completed", item={"id": "item_0", "type": "agent_message", "text": text})
    usage = {"input_tokens": len(prompt) // 4, "cached_input_tokens": 64, "output_tokens": len(text) // 4}
    if hp % 4 == 0:
        event("turn.completed", usage={"input_tokens": 11, "output_tokens": 3})
    event("turn.completed", usage=usage)
    out.write_text(text)
    return 0


def codex_trigger() -> int:
    query = ARGS[-1]
    n = record("")
    project = Path(opt("-C") or ".")
    hq = h(query)
    linked = (project / ".agents/skills/technical-writing/SKILL.md").exists()
    print("fake codex: starting", flush=True)
    event("thread.started", thread_id=f"fake-{hq % 10000}")
    event("turn.started")
    event("item.completed", item={"type": "reasoning", "text": "Maybe technical-writing/SKILL.md applies."})
    if hq % 10 == 1:
        time.sleep(60)
    if linked and h(query, n) % 3:
        event("item.started", item={"type": "command_execution", "status": "in_progress",
                                    "command": "sed -n '1,200p' .agents/skills/technical-writing/SKILL.md"})
    event("item.completed", item={"type": "agent_message", "text": "Start from technical-writing/SKILL.md."})
    event("turn.completed", usage={"input_tokens": 50, "output_tokens": 5})
    return 0


def main() -> int:
    if ARGS == ["--version"]:
        record("")
        print("9.9.9 (Claude Code fake)" if NAME == "claude" else "codex-cli 0.0.0-fake")
        return 0
    if NAME == "claude":
        return claude_stream() if opt("--output-format") == "stream-json" else claude_json()
    return codex_exec() if "-o" in ARGS else codex_trigger()


if __name__ == "__main__":
    sys.exit(main())
