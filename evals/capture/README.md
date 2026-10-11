# Capture

Checks that a change to the eval runner keeps its behaviour byte-identical, without calling a model. `capture.sh` runs the runner's commands against fake `claude` and `codex` CLIs and records everything they produce. `compare.sh` diffs two captures. Use it for refactors and for Python or Pydantic upgrades: any difference is a behaviour change. For an intended change, the diff shows what changed.

It is a comparison between two commits, not a test with expected outputs, and CI does not run it.

## Running

Needs [uv](https://docs.astral.sh/uv/), git, and `python3` 3.9 or later. Run from the repository root; the candidate must be committed, because the capture exports a commit.

```sh
evals/capture/capture.sh main 3.14 /tmp/cap-main
evals/capture/capture.sh main 3.14 /tmp/cap-main-again   # the capture must be stable
evals/capture/compare.sh /tmp/cap-main /tmp/cap-main-again
evals/capture/capture.sh HEAD 3.14 /tmp/cap-head
evals/capture/compare.sh /tmp/cap-main /tmp/cap-head     # IDENTICAL, or the differences
```

`compare.sh` exits 0 when the captures are identical, 1 when they differ, and 2 when either is incomplete. A capture takes about a minute and a half. Compare captures of the same Python version only: argparse wraps help text differently, and `report.json` floats can differ. Run one capture per Python version at a time; captures of the same version share a work directory under `$TMPDIR/tw-capture/` (or `/tmp/tw-capture/`).

To also run `report`, `calibrate`, and `check` on real runs, name them after the output directory, for example `capture.sh main 3.14 /tmp/cap-main v0.1.0 v0.1.0-rc2`. Each run is copied from `evals/runs/` into the export; the original is never written.

If `uv` on your PATH is a version-manager shim that fails outside your shell environment, set `UV=/path/to/uv`.

## What a capture covers

Each command runs in a fresh `git archive` export with `PATH` limited to the fakes, git, `/usr/bin`, and `/bin`; the capture stops if `claude` or `codex` resolves anywhere else. For each command, the capture records:

- stdout, stderr, and the exit code;
- every file written, as `A` (added), `M` (modified), `W` (rewritten with identical content), or `D` (deleted), with the content of each written file;
- every CLI call: argv, working directory, stdin (the full prompt), and the `--output-schema` file.

Temporary paths, durations, timestamps, and timestamp run ids are normalized, so they do not show up as differences.

The commands are `lint`, `selftest`, the `--help` texts, and the error paths for an unknown run and an unknown harness, plus an unknown case for each real run given. Then, with `--jobs 1`, a scenario against the fakes: `generate`, `check`, `judge`, `report`, `generate` and `judge` again, `triggers`, `report`, and `calibrate`, then `all` twice on a second run with cross-family judging.

The fakes reply from their input and from how many times they have seen the same call, so a call can fail first and succeed on a retry. The scenario covers each kind of failed generation, judge output that is invalid once and then valid, judge output that fails both attempts of one `judge` run, and trigger runs that detect the skill, finish without it, or time out.

## Files

| File | Contents |
|---|---|
| `capture.sh` | Exports a commit, runs the commands, records and normalizes a capture. |
| `compare.sh` | Diffs two captures. |
| `fake.py` | The fake `claude` and `codex`. |
| `snap.py` | Records the files each command wrote. |
| `normalize.py` | Removes paths, durations, and timestamps from a capture. |

`fake.py` finds the expectations in a judge prompt by two passages of `evals/judge.md`: the sentence "Each expectation describes part of a good response." and the "## Fidelity" heading with the "For each response, list:" line after it. If you change them, change `fake.py` too.
