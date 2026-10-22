# ADR 0007: job locking

We are going to use Postgres advisory locks for the scheduler. Each job gets a lock key derived from its job name and the scheduler calls `pg_try_advisory_lock(key)` before running it. If it doesn't get the lock it skips the job. This replaces the Redis lock.

This is better and more reliable.

---

Notes pasted from the #platform-scheduling thread (2026-09-18):

- Today each scheduler instance takes a Redis lock with `SET job:<name> <instance> NX PX 600000` before running a job.
- INC-2291: after the Redis primary failed over on 2026-09-02, two scheduler instances both acquired the lock for `nightly-invoices` and invoices were sent twice to ~1,900 customers.
- We already run PostgreSQL 15 for the main app, so advisory locks need no new infrastructure.
- Considered Redlock. Marek is against it: it still relies on timing assumptions, and it would mean running at least 3 independent Redis nodes just for locking.
- Considered "just make every job idempotent". Agreed that's the long-term fix but it's a quarter of work across 14 jobs; not doing it now.
- Priya: advisory locks are session-scoped, so if the scheduler's DB connection drops mid-job the lock is released and another instance could start the same job. Nobody had an answer yet.
- Nobody has measured how many advisory locks we'd hold at peak.
