# technical-writing

An [Agent Skill](https://agentskills.io/specification) that helps coding agents write and revise engineering documents for people: READMEs, guides, API docs, runbooks, RFCs, specs, ADRs, PR descriptions, commit messages, release notes, technical explanations, and handoffs. It works in Claude Code, OpenAI Codex, and Pi.

It holds the agent to two things at once. Every technical fact in the source survives the edit: identifiers, numbers, commands, constraints, caveats, and stated uncertainty. And the result reads like an experienced engineer wrote it for a specific reader, without filler, inflated claims, or formulaic formatting.

## What it does

- Starts from the reader: who they are, what they need to do or decide, and what they already know.
- Follows the repository's conventions first: PR templates, commit history and commitlint config, ADR folders, the CHANGELOG format, neighbouring docs.
- Writes only what the source supports. Missing facts go to the user as a short note, or into the document as marked gaps when it is a draft under review. The agent's own analysis never appears as the author's claim.
- Replaces secrets, credentials, and personal data with placeholders.
- Runs a revision pass for staging, inflation, rhythm by rule, and decoration, acting on patterns rather than banned words.
- Notes, for each artifact type, what its reader looks for first and what drafts most often lose.
- Delivers the document, followed by at most three bullets the user must act on.

## What it doesn't do

- Requirements discovery or planning. That belongs to skills such as `to-spec`; this one writes up what has been decided.
- Documents agents read: skills, prompts, `AGENTS.md`, agent-to-agent handoffs. Use a skill for agent-facing writing, such as `writing-for-agents`.
- Impose one house style. Repository conventions and the author's voice win.
- Enforce anything mechanically. There are no scripts, hooks, or dependencies in the skill.

## Install

Clone the repository anywhere and link only the skill directory, not the repository root, into each harness's skills folder. Linking keeps every harness on the same copy; `git pull` updates it.

```sh
git clone <repository-url> technical-writing
cd technical-writing
git checkout v0.1.0   # optional: pin a release

# Codex and Pi read ~/.agents/skills/
mkdir -p ~/.agents/skills
ln -s "$PWD/skills/technical-writing" ~/.agents/skills/technical-writing

# Claude Code reads ~/.claude/skills/
mkdir -p ~/.claude/skills
ln -s "$PWD/skills/technical-writing" ~/.claude/skills/technical-writing
```

For a single project, link it into `.agents/skills/` (Codex, Pi) or `.claude/skills/` (Claude Code) in the repository instead. Pi also reads `~/.pi/agent/skills/`. Start a new session after installing; harnesses read skill descriptions at startup.

## Use

Agents load the skill on their own when a task matches its description, such as "write the PR description" or "tighten this README". To invoke it explicitly:

| Harness | Command |
|---|---|
| Claude Code | `/technical-writing` |
| Codex | `$technical-writing`, or pick it from `/skills` |
| Pi | `/skill:technical-writing` (needs `enableSkillCommands`) |

## Repository layout

```
skills/technical-writing/SKILL.md          the skill
skills/technical-writing/agents/openai.yaml Codex display name and short description
evals/                                      cases, fixtures, references, runner, results
CHANGELOG.md                                release history
```

## Evaluation

The skill ships with an eval suite that compares it with no guidance and with a one-paragraph writing rule, on seven realistic cases, in both Claude Code and Codex. It checks preserved facts and leaked secrets with code, grades the rest with a blind judge from the other model family, and tests triggering on 20 queries, half of them near misses. [`evals/README.md`](evals/README.md) describes the method and the release gates; [`evals/results/`](evals/results/) holds each release's results.

```sh
python3 evals/run.py lint && python3 evals/run.py selftest
python3 evals/run.py all --run-id smoke --trials 1 --trigger-runs 1
```

## Versioning

The skill follows [Semantic Versioning](https://semver.org/), applied to its behaviour rather than to an API:

- **Major:** the activation scope changes (artifacts added to or dropped from the description), or a guarantee in "Keep the facts" is removed or weakened.
- **Minor:** new behaviour or artifact guidance that existing users would notice in their output.
- **Patch:** wording changes that keep the intended behaviour, and changes to the evals only.

Git tags (`v0.1.0`) and [`CHANGELOG.md`](CHANGELOG.md) are the version record. The skill's frontmatter carries no version field, so there is nothing to keep in sync.

### Releasing

1. Move the `Unreleased` entries in `CHANGELOG.md` under the new version.
2. Run `python3 evals/run.py lint` and `python3 evals/run.py selftest`.
3. For a minor or major release, run the full suite (`python3 evals/run.py all --run-id vX.Y.Z --judge-effort high`), spot-check at least five judged groups, and copy the report into `evals/results/vX.Y.Z.md` with the calibration result. A patch release runs the cases its change affects.
4. Commit, then tag: `git tag -a vX.Y.Z -m "technical-writing vX.Y.Z"`.

## Design decisions

- **One file.** Every run needs the fact rules, the writing guidance, and the output contract, and the artifact notes are one or two lines each. Splitting them into reference files would save a few hundred tokens per activation at the cost of an extra read the agent may skip. Revisit if an artifact needs more than a few lines.
- **A nested skill directory.** The skill lives in `skills/technical-writing/` so installs link only the skill, never the evals or their fixtures, and so the layout matches what multi-harness tooling expects.
- **A narrow description.** About 70 tokens: the artifact types that should trigger it, and one exclusion for agent-facing text, where `writing-for-agents` applies.
- **Rules from evidence.** The skill was drafted after running the cases without it. Baseline models already preserved facts well; what they got wrong was long commentary after the document, bold and emoji, the model's own analysis written as the author's, and, once the first draft was in place, empty sections and TODO stubs. Section checklists were rewritten as reader priorities for that reason.
- **The short rule as the bar.** The skill competes with a ~60-token writing rule, not with nothing. If it stops beating that rule, the rule should replace it.
- **Specifications versus advice.** Conventional Commits rules apply only where the repository uses them; line lengths, mood, and subject case follow the repository's history. Style guidance from individual authors informed the rules but none is treated as universal.

## Sources

The rules are restatements, not copies. These sources informed them:

- [Google developer documentation style guide](https://developers.google.com/style) (CC BY 4.0): conditions before instructions, descriptive link text, sentence-case headings, timeless wording.
- [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/): header grammar and breaking-change markers. The 50/72 line convention comes from [git](https://git-scm.com/docs/git-commit#_discussion) and Tim Pope's 2008 note, not from the specification.
- Martin Fowler, [Architecture Decision Record](https://martinfowler.com/bliki/ArchitectureDecisionRecord.html) and [Excessive Bold](https://martinfowler.com/bliki/ExcessiveBold.html); Michael Nygard, [Documenting Architecture Decisions](https://www.cognitect.com/blog/2011/11/15/documenting-architecture-decisions).
- Zara Cooper, [A practical guide to writing technical specs](https://stackoverflow.blog/2020/04/06/a-practical-guide-to-writing-technical-specs/).
- [blader/humanizer](https://github.com/blader/humanizer) and [addyosmani/clarity](https://github.com/addyosmani/clarity): the fact-preserving rewrite, acting on clusters of tells, and marked gaps instead of invented detail. Their pattern catalogs and examples are not reused.
- [doclandscape: technical writing best practices](https://doclandscape.com/technical-writing-best-practices/): one term per concept, and limits stated with numbers and failure behaviour.
- [NeoLabHQ write-concisely](https://github.com/NeoLabHQ/context-engineering-kit/tree/master/skills/write-concisely) was reviewed and set aside. Its Strunk-derived rules against qualifiers and for end-weighting conflict with calibrated, lead-with-the-point technical writing.

## Limitations

- It improves documents written from the material the agent has. It cannot supply facts the source lacks, and it does not verify technical claims beyond the source.
- Its guidance is tuned for English. It follows the language of the request, but the revision pass's examples are English patterns.
- The eval suite is small (seven cases) and its judges are models; see [`evals/README.md`](evals/README.md#limitations).

## License

[MIT](LICENSE)
