# technical-writing

[![skills.sh](https://skills.sh/b/afreire-laptop/technical-writing)](https://skills.sh/afreire-laptop/technical-writing)

An [Agent Skill](https://agentskills.io/specification) for writing and revising engineering documents that people read: READMEs, guides, API docs, runbooks, RFCs, specs, ADRs, PR descriptions, commit messages, release notes, technical explanations, and handoffs. It loads in Claude Code, OpenAI Codex, and Pi. v0.1.0 is validated for Claude Code only.

The agent keeps every technical fact from the source (identifiers, numbers, commands, constraints, caveats, stated uncertainty) and writes for a specific reader, without filler, inflated claims, or formulaic formatting.

## What it does

- Starts from the reader: who they are, what they need to do or decide, and what they already know.
- Follows the repository's conventions first: PR templates, commit history, commitlint config, ADR folders, the CHANGELOG format, and neighbouring docs.
- Writes only what the source supports. Missing facts go in a short note to the user, or into the document as marked gaps when it is a draft under review. The agent's own analysis never appears as the author's.
- Replaces secrets, credentials, and personal data with placeholders.
- Checks each artifact type for what its reader looks for first and what drafts most often lose.
- Revises formulaic prose by pattern, not by banned words.
- Delivers the document, followed by at most three points the user must act on.

It doesn't plan or gather requirements; use a skill such as `to-spec`. It doesn't write text that agents read, such as skills, prompts, `AGENTS.md`, or agent handoffs; use a skill such as `writing-for-agents`. It doesn't impose a house style: repository conventions and the author's voice win. The skill is instructions only, with no scripts, hooks, or dependencies.

## Install

Install the skill for Claude Code with the [skills CLI](https://github.com/vercel-labs/skills):

```sh
npx skills add afreire-laptop/technical-writing --skill technical-writing -g -a claude-code
```

This copies the skill to `~/.claude/skills/technical-writing`. Keep `-a claude-code`: without it, the CLI installs into every agent it detects, Codex included, and in our evals a one-paragraph writing rule did as well with Codex; Pi was not measured. If you list more than one agent, add `--copy`: with several agents the CLI defaults to symlinks and keeps the main copy in `~/.agents/skills/`, which Codex and Pi also load.

For one project, run the command in the repository without `-g`. The CLI copies the skill to `.claude/skills/technical-writing` and records it in `skills-lock.json`. Run `npx skills update` to update; `-g` or `-p` limits it to global or project skills.

To pin a release, install from its tag. `update` keeps a pinned install on that tag; run `add` again to move it.

```sh
npx skills add https://github.com/afreire-laptop/technical-writing/tree/v0.1.0/skills/technical-writing -g -a claude-code
```

Start a new session after installing.

## Use

The agent loads the skill when a task matches its description, such as "write the PR description" or "tighten this README". To invoke it explicitly:

| Harness | Command |
|---|---|
| Claude Code | `/technical-writing` |
| Codex | `$technical-writing`, or pick it from `/skills` |
| Pi | `/skill:technical-writing` (needs `enableSkillCommands`) |

## Design decisions

- The skill is one file. Every run needs the fact rules, the writing guidance, and the output contract, and each artifact note is a line or two. Reference files would save a few hundred tokens per activation but add a read the agent may skip.
- It lives in `skills/technical-writing/`, so installs get only the skill, never the evals, and the layout matches what multi-harness tools expect.
- The description is about 70 tokens: the artifact types that should trigger it, and one exclusion for agent-facing text.
- The rules come from running the cases without the skill first. Baseline models already kept facts well. They failed on long commentary after the document, bold and emoji, and their own analysis written as the author's. Early drafts of the skill added empty sections and TODO stubs, so section checklists became reader priorities.
- The fact rules have an order: secrets and personal data first, then the facts this reader needs, then writing only what the source supports, then the author's voice. "Keep every fact" alone conflicts with relevance edits and with removing sensitive material.
- The bar is the short rule, not no guidance. If the skill stops beating the rule, the rule should replace it.
- Conventional Commits rules apply only where a repository uses them. Line lengths, mood, and subject case follow the repository's history.

## Sources

The rules restate these sources; nothing is copied.

- [Google developer documentation style guide](https://developers.google.com/style) (CC BY 4.0): conditions before instructions, descriptive link text, sentence-case headings, timeless wording.
- [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/): header grammar and breaking-change markers. The 50/72 line convention comes from [git](https://git-scm.com/docs/git-commit#_discussion) and Tim Pope's 2008 note, not the specification.
- Martin Fowler, [Architecture Decision Record](https://martinfowler.com/bliki/ArchitectureDecisionRecord.html) and [Excessive Bold](https://martinfowler.com/bliki/ExcessiveBold.html); Michael Nygard, [Documenting Architecture Decisions](https://www.cognitect.com/blog/2011/11/15/documenting-architecture-decisions).
- Zara Cooper, [A practical guide to writing technical specs](https://stackoverflow.blog/2020/04/06/a-practical-guide-to-writing-technical-specs/).
- [blader/humanizer](https://github.com/blader/humanizer) and [addyosmani/clarity](https://github.com/addyosmani/clarity): fact-preserving rewrites, acting on clusters of tells, and marked gaps instead of invented detail. Their catalogs and examples are not reused.
- [doclandscape: technical writing best practices](https://doclandscape.com/technical-writing-best-practices/): one term per concept, and limits stated with numbers and failure behaviour.

## Limitations

- It works from the material the agent has. It cannot supply missing facts or verify claims beyond the source.
- Its guidance is tuned for English. It follows the language of the request, but its examples of formulaic prose are English.
- Its benefit over a short writing rule is measured for Claude Code only.
- The eval suite is small (seven cases) and its judges are models; see [`evals/README.md`](evals/README.md#limitations).

## Evaluation

The eval suite compares the skill with no guidance and with a one-paragraph writing rule, on seven cases, in Claude Code and Codex. Code checks preserved facts and leaked secrets; a blind panel of one judge from each model family grades the rest; 20 queries, half of them near misses, test triggering. The verdict fails closed if any result is missing. [`evals/README.md`](evals/README.md) has the method and release gates.

Result for v0.1.0 ([details](evals/results/v0.1.0.md)): with Claude Code, judges preferred the skill over the short rule in 30 of 42 groups, and the rule in 9. With Codex, they preferred the rule, 21 groups to 15, although the skill kept more facts. A person's blind check of five groups agreed with 9 of 10 judge verdicts. For Codex and Pi, put the short rule in the global instructions instead of installing the skill.

```sh
uv run evals/run.py lint && uv run evals/run.py selftest
uv run evals/run.py all --run-id smoke --trials 1 --trigger-runs 1
```

## Repository layout

```
skills/technical-writing/SKILL.md            the skill
skills/technical-writing/agents/openai.yaml  Codex display name and short description
evals/                                       cases, fixtures, references, runner, results
CHANGELOG.md                                 release history
release-please-config.json                   release pull request and changelog settings
.github/workflows/ci.yml                     lint, selftest, type check, and commit messages on every push and pull request
.github/workflows/release.yml                release pull requests and tags
```

## Versioning

Versions follow [Semantic Versioning](https://semver.org/), applied to the skill's behaviour. Commits follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/), and the commit type sets the version:

| Change | Commit | Version |
|---|---|---|
| Artifacts added to or dropped from the description, or a guarantee in "Keep the facts" removed or weakened | `feat(skill)!:`, or a `BREAKING CHANGE:` footer | Major |
| New behaviour or artifact guidance that users would notice in their output | `feat(skill):` | Minor |
| Wording that keeps the behaviour | `fix(skill):` | Patch |
| Evals, docs, or CI only | `test`, `docs`, `ci`, `build`, `refactor`, or `chore` | None |

Changes outside `skills/` make no release because installs copy only the skill directory. CI rejects a commit that changes `skills/` with a type that makes no release, and a commit that makes a release without changing `skills/`.

Git tags and [`CHANGELOG.md`](CHANGELOG.md) record versions. The frontmatter has no version field.

To release:

1. Merge changes to `main`. release-please keeps a pull request titled `chore(release): vX.Y.Z` open, with the next version and its `CHANGELOG.md` entry.
2. For a minor or major release, before merging that pull request, run `uv run evals/run.py all --run-id vX.Y.Z --judge-effort high`. Have a person decide the groups in the packet from `uv run evals/run.py calibrate --run-id vX.Y.Z`. Commit the report and the calibration result to `main` as `evals/results/vX.Y.Z.md`, with the calibration under `## Judge spot-check`. A patch release runs only the cases its change affects.
3. Merge the release pull request. The release workflow checks for the results file and tags `vX.Y.Z`.

If a minor or major release pull request is merged without the results file, nothing is tagged and the release workflow fails on every push. To recover, change the merged pull request's label from `autorelease: pending` to `autorelease: tagged`, commit the results file, and run `gh release create vX.Y.Z --target <commit-with-results>`.

## License

[MIT](LICENSE)
