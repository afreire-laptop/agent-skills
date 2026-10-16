# Changelog

All notable changes to this skill are recorded here, and versions follow the skill's [versioning policy](README.md#versioning). From 0.2.0 on, [release-please](https://github.com/googleapis/release-please) writes each entry from the commits since the previous release, and its open release pull request shows what is not yet released. Changes to the evals, docs, and CI are not listed; the commit history has them. The 0.1.0 entry follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.2.0](https://github.com/afreire-laptop/technical-writing/compare/v0.1.0...v0.2.0) (2026-10-16)


### Features

* **skill:** make output brief by default ([#2](https://github.com/afreire-laptop/technical-writing/issues/2)) ([13e46d6](https://github.com/afreire-laptop/technical-writing/commit/13e46d68af5fa96870baf5ea26714646644b91f5))

## [0.1.0] - 2026-10-10

Released for Claude Code only. Every release gate passed for Claude Code; for Codex the short writing rule did as well as the skill. See [`evals/results/v0.1.0.md`](evals/results/v0.1.0.md).

### Added

- `technical-writing` skill: activation description, fact-preservation rules, reader-first writing guidance, a revision pass for formulaic prose, notes for each artifact type, and an output contract.
- Evaluation suite: seven quality cases with deterministic checks, reference solutions, and judge expectations; a 20-query trigger set; a standard-library runner with a blind two-family judge panel; release gates that fail closed on missing results.
