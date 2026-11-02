# technical-writing

An [Agent Skill](https://agentskills.io/specification) for writing and revising engineering documents that people read: READMEs, guides, API docs, runbooks, RFCs, specs, ADRs, PR descriptions, commit messages, release notes, technical explanations, and handoffs. It loads in Claude Code, OpenAI Codex, and Pi, and is recommended for Claude Code only; see [Evaluation](#evaluation).

The agent keeps every technical fact the reader needs exactly as the source gives it (identifiers, numbers, commands, constraints, caveats, stated uncertainty) and writes for a specific reader, without filler, inflated claims, or formulaic formatting. The skill is instructions only, with no scripts, hooks, or dependencies.

## What it does

- Starts from the reader: who they are, what they need to do or decide, and what they already know.
- Chooses content by what the reader needs to understand, act, or decide, and states each fact once. Accurate detail with a better home, such as how a change was made, a decision's history, test logs, or follow-up work, is linked instead of repeated, and the user can ask for it in an appendix. In its own home that detail stays: an ADR keeps every alternative, a runbook every command.
- Keeps one idea per paragraph: three lines in a commit message, five sentences in an RFC, spec, or ADR, and three sentences elsewhere.
- Follows the repository's conventions first: PR templates, commit history, commitlint config, ADR folders, the CHANGELOG format, and neighbouring docs.
- Writes only what the source supports. Missing facts go in a short note to the user, or into the document as marked gaps when it is a draft under review. The agent's own analysis never appears as the author's.
- Replaces secrets, credentials, and personal data with placeholders.
- Checks each artifact type for what its reader looks for first and what drafts most often lose.
- Revises formulaic prose by pattern, not by banned words.
- Delivers the document, followed by at most three points the user must act on.

## What it doesn't do

- It doesn't plan or gather requirements; use a skill such as `to-spec`.
- It doesn't write text that agents read, such as skills, prompts, `AGENTS.md`, or agent handoffs; use a skill such as `writing-for-agents`.
- It doesn't impose a house style: repository conventions and the author's voice win, except that commit bodies stay short where past ones ran long.

## Install

Install the skill for Claude Code with the [skills CLI](https://github.com/vercel-labs/skills):

```sh
npx skills add afreire-laptop/agent-skills --skill technical-writing -g -a claude-code
```

Start a new session after installing. The [README](../README.md#install) covers project installs, updates, and pinning a release.

Keep `-a claude-code`: without it, the CLI installs into every agent it detects, Codex included. For Codex and Pi, put the [short rule](../evals/suites/technical-writing/conditions/short-rule.md) in the global instructions, such as `~/.codex/AGENTS.md`, instead of installing the skill.

## Use

The agent loads the skill when a task matches its description, such as "write the PR description" or "tighten this README". To invoke it explicitly in Claude Code, run `/technical-writing`; the [README](../README.md#use) has the commands for Codex and Pi.

## Evaluation

v0.4.0, the latest release, [passed every release gate for Claude Code](../evals/suites/technical-writing/results/v0.4.0.md). Judges preferred the skill over the short rule in 68 of 78 groups and the rule in 5, on 12 of 13 cases, with a mean per-case preference of +0.81 (95% CI +0.63 to +0.94). Outputs averaged 446 words under the skill and 642 under the rule.

A person's blind check of five groups agreed with 9 of 10 judge verdicts. These results come from PR #9's final development run, reused instead of a fresh release run; the report's disclosures say what that covers. Triggering was not rerun because the description is unchanged since v0.3.0, whose 60 runs gave a recall of 90% on should-trigger queries and a false-positive rate of 0% on near misses.

Codex was last measured as a writer in [v0.1.0](../evals/suites/technical-writing/results/v0.1.0.md), on seven cases: judges preferred the short rule, 21 groups to 15, although the skill kept more facts. Later releases didn't run the Codex writer, to stay within the Codex plan's usage limits. Pi is unmeasured for quality, so treat it like Codex until measured.

The skill costs about 72 tokens for its description and about 2,410 more when it activates.

The eval suite compares the skill with no guidance and with a one-paragraph writing rule, on thirteen cases. Code checks preserved facts and leaked secrets, a blind panel of one judge from each model family grades the rest, and 20 queries, half of them near misses, test triggering. The verdict fails closed if any result is missing; [`evals/README.md`](../evals/README.md) has the method, release gates, and commands, and [`results/`](../evals/suites/technical-writing/results/) has every release's report.

## Limitations

- It works from the material the agent has. It cannot supply missing facts or verify claims beyond the source.
- Its guidance is tuned for English. It follows the language of the request, but its examples of formulaic prose are English.
- Asked only to polish someone's draft, it trims inside sections but tends to keep the author's sections, even ones the reader doesn't need. Ask for a shorter version when that's what you want.
- Long sources stay long: on the four cases that start from long, accurate material, v0.4.0 wrote 573 to 913 words where the reference solutions took 340 to 457.
- READMEs are its weakest case: in v0.4.0 the judges preferred neither the skill nor the short rule. The spot-checked output wrapped the README in a `markdown` fence around its code blocks, which breaks when rendered, and renamed a title-case heading to sentence case, which breaks links to it.
- It has guidance for issues, but its description doesn't name them, so it may not load for an issue on its own; invoke it explicitly.
- The eval suite is small (thirteen cases) and its judges are models; see [`evals/README.md`](../evals/README.md#limitations).

## Design decisions

- The skill is one file. Every run needs the fact rules, the writing guidance, and the output contract, and each artifact note is a line or two. Reference files would save a few hundred tokens per activation but add a read the agent may skip.
- It lives in `skills/technical-writing/`, so installs get only that directory, never the docs or evals, and the layout matches what multi-harness tools expect.
- The description holds the artifact types that should trigger it and one exclusion for agent-facing text.
- The rules come from running the cases without the skill first. Baseline models already kept facts well. They failed on long commentary after the document, bold and emoji, and their own analysis written as the author's. Early drafts of the skill added empty sections and TODO stubs, so section checklists became reader priorities.
- The fact rules have an order: secrets and personal data first, then the facts this reader needs, then writing only what the source supports, then the author's voice. "Keep every fact" alone conflicts with relevance edits and with removing sensitive material.
- Fidelity and relevance are separate: a fact that appears must be exact, and whether it appears depends on what the reader would get wrong without it. Through v0.3.0 the skill asked for every technical fact in the source to arrive intact, and no eval case had a source that said too much. A content pass now runs before the wording pass.
- The bar is the short rule, not no guidance. If the skill stops beating the rule, the rule should replace it.
- Conventional Commits rules apply only where a repository uses them. Line lengths, mood, and subject case follow the repository's history; body length doesn't. A body says why in one paragraph, with one-line bullets for separate changes: the diff shows how, and the PR holds the testing.
- Paragraph limits follow where a document is read. A commit is read in a 72-column terminal, and an RFC, spec, or ADR carries trade-offs that need more room.

## Sources

The rules restate these sources; nothing is copied.

- [Google developer documentation style guide](https://developers.google.com/style) (CC BY 4.0): conditions before instructions, descriptive link text, sentence-case headings, timeless wording.
- [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/): header grammar and breaking-change markers. The 50/72 line convention comes from [git](https://git-scm.com/docs/git-commit#_discussion) and Tim Pope's 2008 note, not the specification.
- HackerOne, [Writing effective commit messages](https://www.hackerone.com/blog/writing-effective-commit-messages-git-best-practices-and-examples): a body that says why the change is needed, not how it was implemented.
- Martin Fowler, [Architecture Decision Record](https://martinfowler.com/bliki/ArchitectureDecisionRecord.html) and [Excessive Bold](https://martinfowler.com/bliki/ExcessiveBold.html); Michael Nygard, [Documenting Architecture Decisions](https://www.cognitect.com/blog/2011/11/15/documenting-architecture-decisions).
- Zara Cooper, [A practical guide to writing technical specs](https://stackoverflow.blog/2020/04/06/a-practical-guide-to-writing-technical-specs/).
- [blader/humanizer](https://github.com/blader/humanizer) and [addyosmani/clarity](https://github.com/addyosmani/clarity): fact-preserving rewrites, acting on clusters of tells, and marked gaps instead of invented detail. Their catalogs and examples are not reused.
- [doclandscape: technical writing best practices](https://doclandscape.com/technical-writing-best-practices/): one term per concept, and limits stated with numbers and failure behaviour.

## Versioning

The skill follows the repository's [versioning policy](../README.md#versioning). A major release adds artifacts to or drops them from the description, or removes or weakens a guarantee in "Keep the facts". The [changelog](../skills/technical-writing/CHANGELOG.md) lists every release.
