# Evaluations

These evals answer one question: does the `technical-writing` skill improve engineering writing enough to justify its context and maintenance cost, compared with no guidance and with a one-paragraph writing rule?

They follow the practices in Anthropic's [skill authoring guide](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices#evaluation-and-iteration) and [Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents), and OpenAI's [evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices): a baseline without the skill, deterministic graders where possible, calibrated model graders where necessary, multiple trials, isolated runs, and balanced trigger tests.

## Layout

| Path | Contents |
|---|---|
| `cases.json` | Quality cases in skill-creator's `evals.json` shape (`id`, `prompt`, `files`, `expected_output`, `expectations`), plus `checks`: deterministic assertions. |
| `fixtures/` | Input documents for each case. All names, hosts, and credentials are fictional; the credentials are marked `fixture-only` so secret scanners and readers can tell. |
| `reference/` | A reference solution per case. `selftest` proves each one passes every check. |
| `triggers.json` | 10 should-trigger queries and 10 near misses, in skill-creator's trigger eval-set format. |
| `conditions/short-rule.md` | Condition B: the strongest short writing rule we could write, the skill's real competitor. |
| `judge.md` | The judge prompt. |
| `run.py` | The runner's command line. Python 3.10+, run with uv; Pydantic is its one dependency. |
| `runner/` | The runner's modules. `commands.py` has one handler per subcommand and `io.py` the file and process access; the other modules (`models`, `prompts`, `harness`, `checks`, `report`, `gates`, `lint`, `calibrate`, `selftest`) touch no files or processes and import neither. |
| `capture/` | Fake `claude` and `codex` CLIs and a capture script that check, without model calls, that a runner change keeps its behaviour byte-identical. See [`capture/README.md`](capture/README.md). |
| `run.py.lock` | The runner's pinned dependencies, with hashes. Regenerate with `uv lock --script evals/run.py`. |
| `results/` | Committed summaries of release runs. Raw runs go to `runs/`, which is git-ignored. |

## Requirements

[uv](https://docs.astral.sh/uv/), which installs Python 3.10 or later and the locked dependencies on first run, and at least one of the [Claude Code](https://code.claude.com/docs) (`claude`) or [Codex](https://developers.openai.com/codex) (`codex`) CLIs, logged in. The runner calls the CLIs directly, so subscriptions work and no API keys are needed. Cross-family judging needs both.

## Running

```sh
uv run evals/run.py lint        # skill frontmatter, budgets, eval data
uv run evals/run.py selftest    # graders: references pass, raw inputs fail

# Type-check the runner, as CI does.
uvx mypy==2.3.1 --strict --python-executable "$(uv python find --script evals/run.py)" evals/run.py evals/runner

# Smoke run: one trial, 42 generations, 28 judgements, 40 trigger runs.
uv run evals/run.py all --run-id smoke --trials 1 --trigger-runs 1

# Release run: three trials, 126 generations, 84 judgements, 120 trigger runs.
uv run evals/run.py all --run-id v0.1.0 --judge-effort high
```

Every subcommand takes `--run-id`, and `generate`, `judge`, and `triggers` skip work that already succeeded, so an interrupted run resumes where it stopped. Use `--harness claude` or `--harness codex` to run one CLI, `--cases` to select cases, and `--claude-model`, `--codex-model`, `--claude-effort`, `--codex-effort` to pin the writer. Each run records CLI versions, models, the git commit, and hashes of the skill, the short rule, and `cases.json` in `runs/<run-id>/manifest.json`.

## Conditions

Every case runs under three conditions with the same task text:

- **A, no guidance.** The task and its files.
- **B, short rule.** The same, with `conditions/short-rule.md` as writing guidance.
- **C, skill.** The same, with the body of `SKILL.md` as writing guidance.

B and C use the identical wrapper and position, so the only difference between them is the guidance text. Injecting the body measures the skill's content. Whether the skill gets loaded at all is measured separately by the trigger evals.

Each generation runs in a fresh temporary directory. Claude runs with tools and skills disabled. Codex runs with `--ignore-user-config`, an ephemeral session, and a read-only sandbox. Both CLIs still load the user's global instruction file (`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`). That is deliberate: real use has it too, and it applies equally to all three conditions.

## Graders

**Deterministic checks** (`checks` in `cases.json`) cover what code can verify: identifiers, numbers, and commands that must survive; sensitive values that must not appear; Conventional Commits header grammar; filler and stock phrases; length relative to the input. Every case caps the output's length with a `max_word_ratio` check, set at 1.5 times the reference solution's length relative to the input and rounded up to a tenth; `verbose-readme` keeps the 0.6 it had in v0.1.0. A short output that drops a fact still fails its `keeps-*` checks. Types are `contains`, `not_contains`, `regex`, `not_regex`, and `max_word_ratio`. A check can target the whole `output`, the commit `message` (the first fenced code block, or the whole output if there is none), or its `header` (first line). Checks grade the outcome, never the path taken.

**The judges** grade what code cannot. For each case and trial, a panel of two judges, one from each model family, independently receives the task, the source files, the case's `expectations`, and the anonymized outputs, each judge in its own seeded random order. Because both families judge every writer's outputs, a writer effect can be told apart from one judge family's taste; in a design where the judge is always the other family, the two are confounded. Each judge returns:

- `pass`, `fail`, or `unknown` for every expectation, with evidence (skill-creator's grader convention);
- the facts each output invented or dropped, and a fidelity verdict;
- a ranking per dimension (`clarity`, `naturalness`, `brevity`, `conventions`), ties allowed, with reasoning before the ranking.

The report reduces each judgement to one verdict per pair of conditions (C vs B, C vs A, B vs A): the condition that ranks better on more dimensions wins that group. The four dimension rankings from one call are correlated, so they are shown for description but never counted as separate votes. Verdicts are reported per judge and pooled, summed per case, and summarized as the mean per-case preference (−1 to +1) with a 95% bootstrap interval over cases. With seven cases that interval is wide; read it as a measure of how much to trust the direction, not as a significance test. The report also audits position bias, though with few groups it is confounded with which condition happened to be shown first.

Deviation from the cited practice: one judge call grades all dimensions for a group, rather than one isolated call per dimension. This keeps a release run at 84 judgements instead of 336. The mitigations are structured output, reasoning before verdicts, randomized order, a two-family panel, group-level aggregation, and human calibration.

**Calibrate the judge before trusting it.** For a release, `uv run evals/run.py calibrate --run-id <run-id>` writes `runs/<run-id>/calibration/packet.md`: one randomly chosen group per case, up to five, showing the task, the source, and the skill's and the short rule's outputs as X and Y in random order. A person decides each group, then fills in `key.md`, which holds both judges' verdicts, and puts the agreement rate in the results file. If agreement is poor, fix `judge.md` before drawing conclusions.

## Triggers

For each query in `triggers.json`, the runner creates a temporary git repository, links the skill into the project-level skills directory (`.claude/skills/` or `.agents/skills/`), runs the CLI with the query, and stops as soon as the skill is loaded: a `Skill` tool call for Claude, or a read of `technical-writing/SKILL.md` for Codex. The user's global skills stay visible, so the skill has to win against real neighbours such as `writing-for-agents`. The negatives are near misses that share vocabulary with the skill: agent-facing instructions, agent handoffs, commitlint setup, requirements interviews.

## Metrics and the six questions

| Question | Measured by |
|---|---|
| Reader comprehension and clarity | judge `clarity` ranking; expectations about ordering and findability |
| Technical accuracy and information preservation | deterministic `keeps-*` checks; judge fidelity verdicts and invented/omitted facts |
| Naturalness and readability | judge `naturalness`; stock-phrase, emoji, and bold-label checks |
| Appropriate brevity | judge `brevity`; output words; `max_word_ratio` |
| Artifact conventions | judge `conventions`; Conventional Commits and section checks |
| Token and context efficiency | lint (description and body size); measured input-token overhead of B and C over A |

## Release gates

These thresholds were fixed before the first full run; gate 3's aggregation was tightened after the adversarial review (see the results file). The report applies them per harness and fails closed: if any generation, check, judgement, or trigger run the parameters call for is missing or failed, or any gate cannot be evaluated, the verdict is INCOMPLETE. `selftest` verifies this behaviour.

1. **Safety:** under C, no sensitive value from a fixture appears in any output.
2. **Fidelity:** C's deterministic pass rate and judge fidelity pass rate are each at least B's minus 2 percentage points.
3. **Value over the short rule:** pooled over both judges and all trials, more groups prefer C over B than the reverse, and C is net-preferred on at least 4 of the 7 cases.
4. **Triggering:** recall of at least 80% on should-trigger queries and a false-positive rate of at most 20% on near misses.

If gates 1 and 2 pass but gate 3 fails, the short rule does the job, and the recommendation is to adopt it instead of the skill. If gate 1 or 2 fails, the skill needs fixing whatever else passes.

## Adding a case

1. Add a fixture under `fixtures/` with the flaw the case targets and enough specific facts to check.
2. Add the case to `cases.json`. Keep `expectations` checkable by someone reading only the source and the output. Give every check a descriptive `id`.
3. Write the reference solution in `reference/<name>.md`, and add the length cap: 1.5 times the reference's word count divided by the input's, rounded up to a tenth.
4. Run `lint` and `selftest`. The reference must pass every check, and the raw fixture must fail at least one.

## Limitations

- Seven cases are enough to catch large effects, not small ones. Treat differences of a few points as noise, and read the bootstrap interval before the point estimate.
- The fixtures were written by the same author as the skill, and the skill was revised while looking at results on them. There is no held-out set yet. Guard against fitting: nothing in `SKILL.md` may refer to a specific fixture, and the next cases should come from real documents written by other people.
- Model judges share biases with model writers. Cross-family judging and human calibration reduce this but do not remove it.
- Condition C injects the skill body into the prompt instead of loading it through each harness's skill mechanism. The trigger evals cover loading; the combination is checked by hand during release.
