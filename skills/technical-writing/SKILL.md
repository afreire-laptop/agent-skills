---
name: technical-writing
description: Writes and revises engineering documents for human readers, such as READMEs, guides, API docs, runbooks, RFCs, specs, ADRs, PR descriptions, commit messages, release notes, technical explanations, and handoffs to people. Not for text agents read (skills, prompts, AGENTS.md, agent handoffs).
---

# Technical writing

Write for one reader: the person who will use this document to understand, do, or decide something. Assume an experienced engineer with little time and none of your context. The document works when that reader gets everything they need, intact, and nothing that slows them down.

The **source** is everything you were given: draft, notes, diff, thread, code.

## Before writing

1. Name the reader, the action or decision the document serves, and what the reader already knows.
2. Find the conventions this document must follow, and let them override everything below: templates (`.github/PULL_REQUEST_TEMPLATE.md`, `docs/adr/`), commit history and commitlint config, the CHANGELOG format, the structure and terms of neighbouring docs, the language of the request.
3. When revising, keep the author's structure, terms, and voice where they work; change what slows the reader down.

## Keep the facts

These hold whatever else the task asks. When they conflict, the earlier one wins.

1. Secrets, credentials, tokens, and personal data appear nowhere, neither in the document nor in your note. Use placeholders such as `<new-password>` and say how to obtain the real value.
2. Every fact the reader needs from this document survives exactly as given, with its numbers, units, identifiers, commands, code, links, requirement keywords (MUST, SHOULD), caveats, and stated uncertainty. A fact is needed when, without it, the reader would misjudge behaviour, compatibility, or security, do the wrong thing, or decide without a constraint, risk, or reason that bears on the decision; detail they would only consult can sit behind a link to its home (below). Material that doesn't serve this reader, such as an unrelated alert or a side discussion, can go, as can anything the user asks to cut.
3. Write only what the source supports; a plausible guess is a defect the reader cannot see. When a fact the reader needs is missing, name it in your note to the user. Mark it inside the document (`TODO(author): …` or an open question) only in drafts that are reviewed before anyone relies on them, such as an RFC or ADR.
4. The document speaks for its author. Your own analysis, concerns, and suggestions go in your note to the user, offered as suggestions rather than requirements, never into the document as the author's statements or decisions.

Before finishing, compare the draft with the source: every needed fact is present, and nothing is there that the source doesn't support.

## Choose what goes in

Accurate, relevant detail often has a better home than this document: the diff and commits hold how a change was made, an ADR the context and alternatives behind a decision, issues and tickets the plan and follow-up work, test reports the evidence, a README or runbook the procedures. Link or name its home instead of repeating it; with no home to link, leave it out and name it in your note, so the user can ask for an appendix. The same holds inside what stays: give the change, not the code the diff shows, and the test result, not its commands and tool versions. Background and investigation history go too: a reason stays, how it was found goes, such as reproduction steps, log searches, and dead ends.

In its own home, that detail is the content: an ADR keeps every alternative and trade-off, and a runbook every command, check, and rollback step, however long that makes it.

When you revise someone's draft, this applies to their sections too: cut or link whole sections the reader doesn't need, and name them in your note so the author can restore them. State each fact once, in the section where the reader looks for it, such as a test gap that is also a risk; other sections point to it unless the reader must have it there to act. "Keep the facts" still wins: a shorter document that drops a fact the reader needs is wrong.

## Write for the reader

- Lead with what the reader needs first (the decision, the change, the action, the answer) within the artifact's usual structure.
- Keep one idea per paragraph, sized to where the artifact is read: at most three lines in a commit message, footers included; at most five sentences in an RFC, spec, or ADR, where trade-offs need room; at most three sentences everywhere else.
- Be specific: numbers, names, conditions, and failure behaviour ("fails open and emits `ratelimit.redis_unavailable`") instead of qualities ("robust", "reliable").
- Use one term per concept throughout.
- Match form to content: numbered steps for procedures, a table for items compared on the same attributes, bullets for parallel items a reader scans, prose for reasoning and trade-offs. Add headings where the reader needs to navigate.
- Keep emphasis rare. Bold marks a defined term or a warning the reader must not miss, not whole sentences or list labels. Structure comes from headings and lists, without emoji or status icons.

## Revision pass

Reread the draft as its reader, content before wording: ask of each section and paragraph what the reader would get wrong without it, and whether another section or its home already says it. Cut, merge, or link what fails.

Then rewrite any passage that sounds like a template rather than a person:

- staging: openers that announce what follows, "It's not X, it's Y", closing summaries, hopes, offers → start and end with content
- inflation: "seamless", "robust", "powerful", "plays a crucial role" → the specific property or effect
- rhythm by rule: triads for cadence, sentences of uniform shape → the structure the content has
- decoration: emoji, bold-label bullets, title-case headings → plain sentences, sentence-case headings
- confidence the source doesn't support, in either direction → match the source

Act on patterns, not single words; an ordinary "key" or a single dash is fine.

## Artifact notes

What each artifact's reader looks for first, and what drafts most often lose. Use these to check order and coverage, not as a template: cover what the source supports, in the source's or repository's structure when it works, and add no section only to fill it.

- **README or guide:** what it is, requirements, and a command that works, before any background. Commands are copy-pasteable.
- **API reference:** per endpoint or function: parameters with type, required, and default; responses and errors; one example.
- **Runbook:** an on-call engineer under pressure needs prerequisites and timing constraints before step 1, numbered steps with the exact commands, how to verify, and how to roll back.
- **RFC or spec:** reviewers look for the decision and the hard constraints first; drafts most often lose the rejected alternatives with reasons, non-goals, and open questions. Restructure freely; decisions stay the author's.
- **ADR:** a title that states the decision, then the usual order: status (Proposed until accepted), the context that forced the decision, the decision, consequences good and bad, and the alternatives with why each lost. Supersede an accepted ADR with a new one instead of editing it.
- **Issue:** a maintainer triaging it needs a title naming the problem or the request. A bug report gives actual against expected behaviour and one sentence of impact, then the version and environment and the fewest steps that reproduce it; in an issue, reproduction steps are content, not history. Quote only the log lines that show the failure. A feature request states the problem and who has it before any solution, then what done looks like. Keep it to about 150 words of prose, code excluded.
- **PR description:** a title naming the change, then what a reviewer needs: why the change exists (with the issue link) and what it does; its impact on behaviour, contributors, compatibility, or security, migrations included; testing exactly as done, including what was not run; the risks and limitations that matter; and what needs the reviewer's attention or decision. Call out unrelated changes. Link the commits, ADRs, test reports, and issues for walkthroughs, decision history, logs, and follow-ups. Aim for 200 to 400 words of prose, and go beyond that only for needed facts with no other home, such as a breaking change's migration.
- **Review comment:** the weight and the ask first (`Required:`, `Suggestion:`, or `Nit:`), then one reason, then the fix: three sentences and about 60 words of prose; code doesn't count.
- **Commit message:** follow the repository's history and commitlint config for the format, but keep the body short even where past bodies run long. Under Conventional Commits: `type(scope): summary`, with `!` and/or a `BREAKING CHANGE:` footer saying what breaks and how to migrate. Otherwise an imperative summary of about 50 characters and a body wrapped near 72. The body says why in one paragraph; the diff shows how, and testing goes in the PR. List separate changes as one-line `-` bullets. Omit the body when the summary and footers carry every fact the reader needs.
- **Release notes:** written for the release's users: breaking changes and required actions first, then changes in user terms, with issue or PR links.
- **Explanation or handoff:** the answer or current state first, then the reasoning. A handoff names decisions made, open work and blockers, and where things live.

## Output

Give the document itself. Everything inside it addresses its reader. After it, and clearly separated from it, add a note to the user only for what they must act on (missing facts, content you removed, claims to verify): at most three one-sentence bullets and about 50 words in all, each the action and its reason. Point to gaps the document already marks instead of restating them.
