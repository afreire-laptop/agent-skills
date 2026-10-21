Title: Upload attachments directly to S3 with presigned POSTs

## What and why

Uploads over about 25 MB time out, because every upload streams through the API and the gateway cuts requests off at 30 seconds; large uploads also spike API memory (#902). Clients now upload straight to S3, as ADR-0021 (`docs/adr/0021-attachment-uploads.md`, Accepted) describes:

- `POST /v2/attachments/presign` returns a presigned S3 POST for one object key, valid for 10 minutes and limited to 200 MB.
- `POST /v2/attachments/{id}/confirm` queues the file for the virus scan. Until the scan passes, the attachment is pending and `GET /v2/attachments/{id}` returns 409; in the load test the scan took 38 s at p95.
- Migration `20261019_attachment_status.sql` adds `attachments.status` and `scanned_at`, and backfills existing rows to `clean`, since the old flow scanned before saving.

Breaking: `POST /v1/attachments` now returns `Deprecation` and `Sunset: 2027-03-01` headers and stops working on that date. Mobile apps before 4.2 still use it.

Workspaces on custom domains, about 30 enterprise customers, can't upload until their domains are added to the bucket's CORS rules; `docs/runbooks/attachments.md` says how.

Security: the bucket blocks public access, and the bucket policy plus the conditions the API signs are the boundary. Downloads go through the API, which checks workspace membership before issuing a 60-second signed URL. The content type is declared by the client, so the scanner, which checks magic bytes, is what catches a mislabelled file.

## Testing

- Unit 1,204/1,204, integration 86/86 against LocalStack, and the Playwright upload suite 14/14 in Chrome and Firefox.
- Load test on staging, 50 concurrent 150 MB uploads: all finished, and API memory stayed at about 310 MB, where the old flow peaked at 2.1 GB.
- The migration ran against a staging snapshot.
- Not tested: Safari, the mobile apps, and a scan backlog over 10,000 files.

For reviewers: is 2027-03-01 long enough for the mobile team (#915), and should the 200 MB limit vary by plan (#916)?

Closes #902

## Checklist

- [x] Tests added or updated
- [x] Docs updated
- [x] Breaking changes noted in CHANGELOG.md
