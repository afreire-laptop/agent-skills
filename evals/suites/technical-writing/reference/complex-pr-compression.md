Title: Add a shared remote build cache

Adds a remote cache to `kb build` (ADR-0012, Phase 1). CI builds on `main` upload their outputs to `cache.kestrel.example`, and other builds, in CI or on a contributor's machine, download outputs whose inputs haven't changed instead of rebuilding them. CI builds take 14 minutes at p50, mostly rebuilding packages nobody touched.

## Impact

- The Node floor goes from `>=20` to `>=22.12`; on older versions `pnpm install` fails.
- Without a cache token, `kb build` builds locally as before. `kb cache login` fetches a read-only token through SSO and turns the cache on; the first build after it downloads up to 2.3 GB.
- `@kestrel/build-config` gets a minor release for its new optional `remoteCache` key. No other published package changes.
- The new `cache-check` job verifies artifact signatures on pull requests. It is not a required check.

## Security

Only CI on `main` can write: the cache server accepts uploads only with the write token in the `cache-write` GitHub environment, and the bucket policy lets only the server put objects. Pull requests and contributors read with read-only tokens, and pull requests from forks get none. The client also discards any artifact whose signature doesn't verify; that check is defence in depth, not the boundary.

## Testing

- `kb` tests: 412 of 412 pass, including 48 integration tests against a local MinIO server.
- A five-day shadow trial on CI hit the cache 81% of the time, and no downloaded artifact differed from a local build. Projected p50 CI build time is about 5 minutes 30 seconds.
- Tampered and unsigned artifacts were discarded, and an upload with a read-only token returned 403.
- Not tested: Windows, the Linux ARM64 runners, a cache server failover, and a full build on Node 22.

Details are in `docs/build-cache/testing.md`.

## Limitations

- The cache key covers only the environment variables listed in `kb.json`, so a build that reads an unlisted variable can get a stale artifact. Nothing checks the lists yet (#337).
- If the cache server is unreachable, each build waits 10 seconds once, then falls back to building locally.
- If the private signing key leaks, forged artifacts are accepted until it is rotated (`docs/build-cache/runbook.md`).

## For reviewers

ADR-0012 is In Review: please accept or reject it as part of this review. Fork read access (#331) and making `cache-check` required (#332) can be decided later.

Closes #318
