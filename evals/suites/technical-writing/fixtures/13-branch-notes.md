Branch: `feat/presigned-uploads` → `main` in `quill`

`.github/PULL_REQUEST_TEMPLATE.md`:

```markdown
## What and why

## Testing

## Checklist

- [ ] Tests added or updated
- [ ] Docs updated
- [ ] Breaking changes noted in CHANGELOG.md
```

Issue #902, "Large attachments fail to upload": uploads over about 25 MB time out, because every upload streams through the API, and the gateway cuts requests off at 30 seconds. API pods also spike in memory during big uploads; two were OOM-killed last week.

`git log --format='%s%n%n%b' main..HEAD`:

```
feat(api): add POST /v2/attachments/presign

Returns a presigned S3 POST for one object key,
attachments/<workspace-id>/<uuid>, valid for 10 minutes. The API signs
a policy only for content types in ALLOWED_ATTACHMENT_TYPES, and the
policy limits the upload to 200 MB. The bucket stays private.

feat(api): confirm uploads and gate them on the virus scan

POST /v2/attachments/{id}/confirm checks the object's size and type
with a HEAD request and queues it for the ClamAV worker. Until the scan
passes, the attachment is pending and GET /v2/attachments/{id} returns
409. The scanner checks the file's magic bytes, so a wrong declared
content type is caught there.

feat(web): upload attachments through presigned URLs

The editor uploads straight to S3 and shows real progress. Each upload
is retried 4 times with a 500 ms base delay. A pending attachment shows
"Scanning…" until the scan passes.

chore(db): add attachments.status and scanned_at

Migration 20261019_attachment_status.sql. Existing rows are backfilled
to status 'clean', because the old flow scanned files before saving them.

feat(api)!: deprecate POST /v1/attachments

The v1 endpoint still works, and now returns Deprecation and
Sunset: 2027-03-01 headers. Mobile apps before 4.2 still use it.

BREAKING CHANGE: POST /v1/attachments stops working on 2027-03-01.

docs: update ADR-0021 and add the upload runbook
```

Notes on ADR-0021 (status Accepted; the architecture group approved the change on 2026-10-14): the first version proposed streaming uploads through the API. We dropped that because the gateway's 30-second limit applies to streamed requests too. A multipart upload handled by the API was rejected because it still holds parts in API memory. Running a tus server was rejected because it adds a service nobody on the team runs. The ADR now records presigned POSTs, the scan gate, and downloads through 60-second signed GET URLs issued by the API.

Security, from my notes:

- The bucket blocks all public access. Clients can only write the one key in their signed policy, for 10 minutes, up to 200 MB.
- Nothing can be downloaded until the scan passes, and downloads go through the API, which checks workspace membership before issuing a 60-second signed URL.
- Bucket CORS allows `https://app.quill.example` only. Enterprise customers on custom domains (about 30 workspaces) can't upload until we add their domains; the runbook says how.
- The real boundary is the bucket policy plus the conditions the API signs. The content-type allowlist in the policy is a convenience, since the client declares the type; the scanner is what catches a mislabelled file.

Testing log:

- `pnpm test`: 1,204/1,204 unit tests passed (Node 22.11, macOS 15.6).
- `pnpm test:integration`: 86/86 against LocalStack 3.8.
- Playwright upload suite: 14/14 in Chrome 131 and Firefox 133. Not run in Safari.
- Load test on staging: 50 concurrent 150 MB uploads. API memory stayed flat at about 310 MB, where the old flow peaked at 2.1 GB. All 50 uploads finished; p95 time from confirm to scan passed was 38 s. The run cost about $3.12 in S3 requests and transfer.
- Migration run against last night's staging snapshot (4.1 million rows backfilled in 47 s).
- Not tested: the mobile apps (they still use v1), and the scan queue with a backlog over 10,000 files.

Open questions for reviewers:

- Is 2027-03-01 long enough for the mobile team to move everyone to 4.2 or later? Tracked in #915.
- Should the 200 MB limit vary by plan? Tracked in #916.

Follow-ups I filed: #917 run the Playwright suite in Safari, #918 move the mobile apps to v2, #919 delete the v1 upload code after the sunset, #920 a dashboard for scan latency, #921 the flaky Playwright test in the comments panel, #922 upgrade LocalStack to 4.x.

Docs: `docs/runbooks/attachments.md` (CORS domains, stuck scans), ADR-0021 at `docs/adr/0021-attachment-uploads.md`. CHANGELOG.md has the v1 deprecation under Unreleased.
