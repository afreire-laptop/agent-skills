# agent-skills

[![skills.sh](https://skills.sh/b/afreire-laptop/technical-writing)](https://skills.sh/afreire-laptop/technical-writing)

[Agent Skills](https://agentskills.io/specification) for engineering work. Each skill has its own eval suite, version, and releases, and loads in Claude Code, OpenAI Codex, and Pi; its docs say which agents it is measured for.

## Skills

| Skill | What it does | Docs | Changelog |
|---|---|---|---|
| [`technical-writing`](skills/technical-writing/SKILL.md) | Writes and revises engineering documents that people read, such as READMEs, runbooks, ADRs, PR descriptions, and commit messages. Measured for Claude Code. | [docs](docs/technical-writing.md) | [changelog](skills/technical-writing/CHANGELOG.md) |

## Install

Install a skill with the [skills CLI](https://github.com/vercel-labs/skills), naming the skill with `--skill` and the agent with `-a`:

```sh
npx skills add afreire-laptop/agent-skills --skill technical-writing -g -a claude-code
```

This copies the skill's directory to `~/.claude/skills/technical-writing`. Without `-a`, the CLI installs into every agent it detects. If you list more than one agent, add `--copy`: with several agents the CLI defaults to symlinks and keeps the main copy in `~/.agents/skills/`, which Codex and Pi also load.

For one project, run the command in the repository without `-g`. The CLI copies the skill to `.claude/skills/<skill>` and records it in `skills-lock.json`. Run `npx skills update` to update; `-g` or `-p` limits it to global or project skills.

To pin a release, install from its tag. `update` keeps a pinned install on that tag; run `add` again to move it.

```sh
npx skills add https://github.com/afreire-laptop/agent-skills/tree/technical-writing-v0.4.0/skills/technical-writing -g -a claude-code
```

This repository was named `technical-writing` until 2026-10-22. Commands and installs that use the old name keep working through GitHub's redirect.

Start a new session after installing.

## Use

An agent loads a skill when a task matches its description. To invoke one explicitly:

| Harness | Command |
|---|---|
| Claude Code | `/<skill>` |
| Codex | `$<skill>`, or pick it from `/skills` |
| Pi | `/skill:<skill>` (needs `enableSkillCommands`) |

## Evaluation

Each skill has an eval suite in `evals/suites/<skill>/`, run by the shared runner in `evals/`. [`evals/README.md`](evals/README.md) has the method and release gates, and each skill's docs summarize its results.

```sh
uv run evals/run.py lint && uv run evals/run.py selftest
```

## Repository layout

```
skills/<skill>/                  the skill: SKILL.md, agent metadata, and its CHANGELOG.md
docs/<skill>.md                  what the skill does, its design decisions, sources, limitations, and results
evals/                           the eval runner and its tools
evals/suites/<skill>/            the skill's eval cases, fixtures, references, and results
release-please-config.json       release pull request and changelog settings, one package per skill
.github/workflows/ci.yml         lint, selftest, type check, and commit messages on every push and pull request; release evidence on pull requests
.github/workflows/release.yml    release pull requests and tags
```

Installs copy only `skills/<skill>/`, never the docs or evals.

## Versioning

Each skill has its own version. Versions follow [Semantic Versioning](https://semver.org/), applied to the skill's behaviour. Commits follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/): the type sets the version, and the scope names the skill.

| Change | Commit | Version |
|---|---|---|
| The description stops covering a task it covered, or a guarantee is removed or weakened; each skill's docs say what counts | `feat(<skill>)!:`, or a `BREAKING CHANGE:` footer | Major |
| New behaviour that users would notice in their output | `feat(<skill>):` | Minor |
| Wording that keeps the behaviour | `fix(<skill>):` | Patch |
| Evals, docs, or CI only | `test`, `docs`, `ci`, `build`, `refactor`, or `chore` | None |

Installs copy only a skill's directory, so changes outside `skills/<skill>/` make no release. release-please gives a commit to every skill whose files it changes, whatever its scope, so a releasing commit changes exactly one skill and names it as its scope. CI rejects a commit that breaks either rule, and a commit that changes a skill with a type that makes no release; a skill's `CHANGELOG.md` doesn't count as a change to the skill.

Git tags and each skill's changelog, such as [`skills/technical-writing/CHANGELOG.md`](skills/technical-writing/CHANGELOG.md), record versions. Tags name the skill, as in `technical-writing-v0.5.0`. Releases through 0.4.0 were tagged `vX.Y.Z`, and `technical-writing-v0.4.0` marks the same commit as `v0.4.0`. The frontmatter has no version field.

To release a skill:

1. Merge changes to `main`. release-please keeps one pull request open per skill with unreleased changes, titled `chore(release): <skill> vX.Y.Z`, with the next version and its changelog entry.
2. For a minor or major release, merge the evidence into `main` through a pull request before merging the release pull request. Every skill needs a trigger suite, `evals/suites/<skill>/triggers.json`, and the report of a run as `evals/suites/<skill>/results/vX.Y.Z.md`. When the suite also has quality cases, as technical-writing's does, run `uv run evals/run.py all --run-id vX.Y.Z --judge-effort high`, have a person decide the groups in the packet from `uv run evals/run.py calibrate --run-id vX.Y.Z`, and put the result under `## Judge spot-check` in the report. A patch release runs only the cases its change affects.
3. release-please doesn't rebuild its pull request when the results merge, because a `docs` commit leaves the changelog unchanged. Run `gh pr update-branch <number> --rebase` to put the release pull request on top of the results.
4. Merge the release pull request once its `release-evidence` check passes. The release workflow checks again and tags `<skill>-vX.Y.Z`.

The `release-evidence` ruleset requires that check on `main` with no bypass, so every change, results included, reaches `main` through a pull request. If GitHub Actions is down, an admin can disable the ruleset in the repository settings.

If a minor or major release pull request is merged without the results file, nothing is tagged, and the release workflow fails on every push for every skill until you recover. To recover, change the merged pull request's label from `autorelease: pending` to `autorelease: tagged`, merge the results file through a pull request, and run `gh release create <skill>-vX.Y.Z --target <commit-with-results>`. Then rerun the release workflow run that the results commit started: it began before the tag existed, so it failed.

## License

[MIT](LICENSE)
