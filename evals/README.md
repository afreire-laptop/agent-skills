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
| `run.py` | The runner. Python 3.10+, standard library only. |
| `results/` | Committed summaries of release runs. Raw runs go to `runs/`, which is git-ignored. |

## Requirements

Python 3.10 or later, and at least one of the [Claude Code](https://code.claude.com/docs) (`claude`) or [Codex](https://developers.openai.com/codex) (`codex`) CLIs, logged in. The runner calls the CLIs directly, so subscriptions work and no API keys are needed. Cross-family judging needs both.

## Running

```sh
python3 evals/run.py lint        # skill frontmatter, budgets, eval data
python3 evals/run.py selftest    # graders: references pass, raw inputs fail

# Smoke run: one trial per condition, about 42 generations and 14 judge calls.
python3 evals/run.py all --run-id smoke --trials 1 --trigger-runs 1

# Release run: three trials, about 126 generations, 42 judge calls, 120 trigger runs.
python3 evals/run.py all --run-id v0.1.0 --judge-effort high
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

**Deterministic checks** (`checks` in `cases.json`) cover what code can verify: identifiers, numbers, and commands that must survive; sensitive values that must not appear; Conventional Commits header grammar; filler and stock phrases; length relative to the input. Types are `contains`, `not_contains`, `regex`, `not_regex`, and `max_word_ratio`. A check can target the whole `output`, the commit `message` (the first fenced code block, or the whole output if there is none), or its `header` (first line). Checks grade the outcome, never the path taken.

**The judge** grades what code cannot. For each case and trial, a model from the *other* family (Codex grades Claude's outputs and vice versa) receives the task, the source files, the case's `expectations`, and the anonymized outputs in a seeded random order. It returns:

- `pass`, `fail`, or `unknown` for every expectation, with evidence (skill-creator's grader convention);
- the facts each output invented or dropped, and a fidelity verdict;
- a ranking per dimension (`clarity`, `naturalness`, `brevity`, `conventions`), ties allowed, with reasoning before the ranking.

The report turns the rankings into pairwise wins, ties, and losses (C vs B, C vs A, B vs A) and audits position bias: how often the first-shown output ranked best.

Deviation from the cited practice: one judge call grades all dimensions for a group, rather than one isolated call per dimension. This keeps a release run at 42 judge calls instead of 168. The mitigations are structured output, reasoning before verdicts, randomized order, cross-family judging, and human calibration.

**Calibrate the judge before trusting it.** For a release, a person reads at least five judged groups in `runs/<run-id>/judge/`, records whether they agree with each verdict, and puts the agreement rate in the results file. If agreement is poor, fix `judge.md` before drawing conclusions.

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

These thresholds were fixed before the first run. The report applies them per harness.

1. **Safety:** under C, no sensitive value from a fixture appears in any output.
2. **Fidelity:** C's deterministic pass rate and judge fidelity pass rate are each at least B's minus 2 percentage points.
3. **Value over the short rule:** across dimensions and trials the judge prefers C over B more often than the reverse, and C is net-preferred on at least 4 of the 7 cases.
4. **Triggering:** recall of at least 80% on should-trigger queries and a false-positive rate of at most 20% on near misses.

If gates 1 and 2 pass but gate 3 fails, the short rule does the job, and the recommendation is to adopt it instead of the skill. If gate 1 or 2 fails, the skill needs fixing whatever else passes.

## Adding a case

1. Add a fixture under `fixtures/` with the flaw the case targets and enough specific facts to check.
2. Add the case to `cases.json`. Keep `expectations` checkable by someone reading only the source and the output. Give every check a descriptive `id`.
3. Write the reference solution in `reference/<name>.md`.
4. Run `lint` and `selftest`. The reference must pass every check, and the raw fixture must fail at least one.

## Limitations

- Seven cases are enough to catch large effects, not small ones. Treat differences of a few points as noise.
- The fixtures were written by the same author as the skill. Guard against fitting the skill to them: nothing in `SKILL.md` may refer to a specific fixture, and new cases should come from real documents that went wrong.
- Model judges share biases with model writers. Cross-family judging and human calibration reduce this but do not remove it.
- Condition C injects the skill body into the prompt instead of loading it through each harness's skill mechanism. The trigger evals cover loading; the combination is checked by hand during release.
