# Changelog

All notable changes to this skill are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow the skill's [versioning policy](README.md#versioning).

## [Unreleased]

### Added

- CI workflow that runs `lint` and `selftest` on Python 3.10 and 3.14, and type-checks the eval runner with `mypy --strict`.

### Changed

- Shorter, plainer README, revised with the skill itself.
- The eval runner runs with uv (`uv run evals/run.py`) and depends on Pydantic, pinned with hashes in `evals/run.py.lock`. It validates every JSON file and CLI output it reads, so malformed input fails with a validation message instead of a traceback. Prompts, the judge schema, and seeds are unchanged, so earlier runs stay valid.

## [0.1.0] - 2026-10-10

Released for Claude Code only. Every release gate passed for Claude Code; for Codex the short writing rule did as well as the skill. See [`evals/results/v0.1.0.md`](evals/results/v0.1.0.md).

### Added

- `technical-writing` skill: activation description, fact-preservation rules, reader-first writing guidance, a revision pass for formulaic prose, notes for each artifact type, and an output contract.
- Evaluation suite: seven quality cases with deterministic checks, reference solutions, and judge expectations; a 20-query trigger set; a standard-library runner with a blind two-family judge panel; release gates that fail closed on missing results.
