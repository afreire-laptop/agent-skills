Title: Add a shared remote build cache

## Summary

This PR adds a remote cache to `kb build`, as proposed in ADR-0012 (Phase 1). CI builds on `main` upload their build outputs to a shared cache at `cache.kestrel.example`, and every other build, in CI or on a contributor's machine, downloads outputs whose inputs haven't changed instead of rebuilding them. CI builds currently take 14 minutes at p50 (#318), and most of that time goes to rebuilding packages nobody touched.

Nothing changes for contributors who do nothing: without a cache token, `kb build` builds locally as it does today. Running `kb cache login` once fetches a read-only token through SSO and turns the cache on. The one change every contributor sees is the Node floor, which goes from `>=20` to `>=22.12`.

## Review by commit

The six commits are meant to be reviewed in order.

1. `build: require node 22.12`. Root `engines.node` goes from `>=20` to `>=22.12`, the first 22.x release that loads ES modules with `require()` without a flag, which the cache client's dependencies need. On older versions, `pnpm install` stops with an engines error. CI already runs Node 24, so only `.nvmrc` and the engines field change.
2. `feat(build): add the remote cache client`. `packages/kb/src/cache/client.ts` computes a cache key as the SHA-256 of the package's source files, its resolved dependency graph, the platform and Node major version, and the environment variables listed in the `env` array of `kb.json`. Artifacts are tarballs, uploaded in 8 MB chunks. The client retries a failed request 3 times with 250 ms backoff. If the server hasn't answered within 10 seconds, the client stops using the cache for the rest of the build, which continues locally.
3. `feat(build): sign and verify cache artifacts`. Uploads carry an Ed25519 signature over the artifact and its cache key. The client verifies the signature on every download with the public key committed in `kb.json`, discards any artifact that is unsigned or doesn't match, and builds that package locally. The private key is `KB_CACHE_SIGNING_KEY`, and only the `cache-write` environment has it.
4. `feat(build): add kb cache login`. Uses the existing SSO device flow to get a read-only token and stores it in `~/.config/kb/token` with mode 0600. Tokens expire after 30 days.
5. `docs: add the build cache docs and update ADR-0012`. Adds `docs/build-cache/` (runbook, security model, testing) and updates the ADR; see below.
6. `ci: upload to the build cache from main`. The `build` workflow uploads on pushes to `main` using the write token in the `cache-write` environment. A new `cache-check` job verifies signatures on pull requests. It is not a required check.

`@kestrel/build-config` gets a minor changeset because its schema gains the optional `remoteCache` key. No other published package changes.

## ADR-0012 changes

The ADR first proposed that clients read and write an S3 bucket directly. The spike in August (#297) showed that this needs a credential with write access on every contributor machine, so the ADR now puts a small cache server in front of the bucket, and only CI can write. The alternatives it now records:

- The vendor-hosted cache: rejected because build artifacts would leave our EU region, which the data-residency policy doesn't allow.
- A per-runner disk cache: measured in the spike at a 34% hit rate, because runners are ephemeral and rarely reuse a disk.
- Splitting the CI build into more parallel jobs: cuts wall time but not compute, and does nothing for local builds.

The ADR's decision 4 (who may write) changed from "any authenticated client" to "CI on `main` only". Decision 6, cache retention, stays at 30 days, with eviction left to the bucket's lifecycle rule. The ADR is still In Review.

## Security model

There are three layers. The cache server accepts uploads only with a write token, which exists only in the `cache-write` GitHub environment, limited to workflows on `main`. The bucket policy lets only the cache server's IAM role put objects, so nothing can write around the server. Pull request builds and contributors get read-only tokens, and pull requests from forks get no token, so they build without the cache. Finally, the client verifies the signature of every artifact.

The server's token check and the bucket policy are the real boundary. The signature check is defence in depth: it catches an artifact that was corrupted or replaced in the bucket, but anyone with the private key could forge one, which is why the key is only in the `cache-write` environment. If the private key leaks, rotate it as `docs/build-cache/runbook.md` describes and purge the bucket; until then, a forged artifact would be accepted.

## How it was tested

- `pnpm --filter kb test`: 412 of 412 passed on macOS 15.6 with Node 24.10.0, including 48 integration tests that run the client against a local MinIO server.
- Shadow trial: from 6 to 10 October, CI builds on `main` uploaded their outputs, and every CI build downloaded the artifacts that matched but still built everything and compared the two. Over 1,214 builds, 81% of package builds found a matching artifact, and no downloaded artifact differed from a local build. Projected from the hits, p50 CI build time would drop from 14 minutes to about 5 minutes 30 seconds. The trial cost $18.40 in extra runner minutes.
- Tampering: an artifact with one byte changed, and one with its signature removed, were both discarded and rebuilt locally.
- Token scope: an upload with a contributor's read-only token returned 403.
- `kb cache login`: tried by three contributors on macOS and one on Ubuntu 24.04.

Not tested: Windows, the Linux ARM64 runners, a cache server failover, and a full build on Node 22 (CI and the trial ran Node 24).

More detail in `docs/build-cache/testing.md`.

## Risks

- Contributors on Node below 22.12 can't install dependencies until they upgrade.
- The first build after `kb cache login` downloads up to 2.3 GB.
- The cache key includes only the environment variables listed in `kb.json`. A package whose build reads an unlisted variable can get an artifact built with a different value. The docs say to list such variables, but nothing checks it.
- If the cache server is down, each build waits out the 10-second timeout once and then builds locally, so builds get slower, not broken.

## Open questions for reviewers

1. Please accept or reject ADR-0012 as part of reviewing this PR. If you accept it, I'll change its status before merging.
2. Should pull requests from forks get read access? Today they get no token (#331).
3. Should `cache-check` become a required check once it has a week of runs (#332)?

## Follow-ups

Filed as issues:

- #333 a Grafana dashboard for hit rate and download time
- #334 move the docs site build to the cache
- #335 delete `scripts/warm-cache.sh`, which the cache replaces
- #336 the flaky snapshot test in `@kestrel/charts` that failed twice during the trial
- #337 check the `env` lists in `kb.json` against the variables each build reads
- #338 cache test results as well as build outputs

Closes #318
