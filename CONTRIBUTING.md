# Contributing

## Evaluation

Each skill has an eval suite in `evals/suites/<skill>/`, run by the shared runner in `evals/`. [`evals/README.md`](evals/README.md) has the method and release gates, and each skill's docs summarize its results.

To lint the skills and eval data and test the graders without calling a model, install [uv](https://docs.astral.sh/uv/) and run:

```sh
uv run evals/run.py lint && uv run evals/run.py selftest
```

## Repository layout

| Path | Contents |
|---|---|
| `skills/<skill>/` | The skill: `SKILL.md`, agent metadata, and its `CHANGELOG.md` |
| `docs/<skill>.md` | What the skill does, its design decisions, sources, limitations, and results |
| `evals/` | The eval runner and its tools |
| `evals/suites/<skill>/` | The skill's eval cases, fixtures, references, and results |
| `release-please-config.json` | Release pull request and changelog settings, one package per skill |
| `.release-please-manifest.json` | The version release-please last set for each skill |
| `.github/workflows/ci.yml` | Lint, selftest, type check, and commit messages on pushes to `main` and on pull requests; release evidence on pull requests |
| `.github/workflows/release.yml` | Release pull requests and tags |
| `.github/scripts/` | The commit message and release evidence checks those workflows run |

## Versioning

Each skill's version follows [Semantic Versioning](https://semver.org/), applied to the skill's behaviour. Git tags and each skill's changelog record versions; the frontmatter has no version field.

Tags name the skill, as in `technical-writing-v0.5.0`. Releases through 0.4.0 were tagged `vX.Y.Z`, and `technical-writing-v0.4.0` marks the same commit as `v0.4.0`.

### Commits

Commits follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/): the type sets the version, and the scope names the skill.

| Change | Commit | Version |
|---|---|---|
| The description stops covering a task it covered, or a guarantee is removed or weakened; each skill's docs say what counts | `feat(<skill>)!:`, or a `BREAKING CHANGE:` footer | Major |
| New behaviour that users would notice in their output | `feat(<skill>):` | Minor |
| Wording that keeps the behaviour | `fix(<skill>):` | Patch |
| Evals, docs, or CI only | `test`, `docs`, `ci`, `build`, `refactor`, `perf`, `style`, or `chore` | None |

CI checks each commit, and the pull request title because a squash merge uses it as the commit header:

- The header is `type(scope): summary`, at most 72 characters. The summary starts with a lowercase letter or a digit and has no final period.
- A commit that changes a skill is `feat`, `fix`, or `revert`, or marks a breaking change with `!`. It changes only that skill, and its scope is the skill's name. A skill's `CHANGELOG.md` doesn't count as a change to the skill.
- Any other commit is none of those, so it makes no release.

Installs copy only a skill's directory, so changes outside `skills/<skill>/` make no release. release-please gives a commit to every skill whose files it changes, whatever its scope, so a commit that changed two skills would release both.

### Release a skill

1. Merge changes to `main`. release-please keeps one pull request open per skill with unreleased changes, titled `chore(release): <skill> vX.Y.Z`, with the next version and its changelog entry.
2. For a minor or major release, merge the evidence into `main` through a pull request before merging the release pull request. A patch release runs only the cases its change affects.
   - Every skill needs a trigger suite, `evals/suites/<skill>/triggers.json`, and the report of a run as `evals/suites/<skill>/results/vX.Y.Z.md`.
   - When the suite also has quality cases, as technical-writing's does, run `uv run evals/run.py all --run-id vX.Y.Z --judge-effort high`, have a person decide the groups in the packet from `uv run evals/run.py calibrate --run-id vX.Y.Z`, and put the result under `## Judge spot-check` in the report.
3. Run `gh pr update-branch <number> --rebase` to put the release pull request on top of the results. release-please doesn't rebuild its pull request when the results merge, because a `docs` commit leaves the changelog unchanged.
4. Merge the release pull request once its `release-evidence` check passes. The release workflow checks again and tags `<skill>-vX.Y.Z`.

The `release-evidence` ruleset requires that check on `main` with no bypass, so every change, results included, reaches `main` through a pull request. If GitHub Actions is down, an admin can disable the ruleset in the repository settings.

### Recover a release merged without results

If a minor or major release pull request is merged without the results file, nothing is tagged, and the release workflow fails on every push for every skill until you recover:

1. Change the merged pull request's label from `autorelease: pending` to `autorelease: tagged`.
2. Merge the results file through a pull request.
3. Run `gh release create <skill>-vX.Y.Z --target <commit-with-results>`.
4. Rerun the release workflow run that the results commit started. It began before the tag existed, so it failed.
