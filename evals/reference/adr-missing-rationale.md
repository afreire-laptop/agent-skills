# ADR 0007: Use PostgreSQL advisory locks for scheduler job locking

**Status:** Proposed

## Context

Each scheduler instance currently takes a Redis lock before running a job (`SET job:<name> <instance> NX PX 600000`). In INC-2291, after the Redis primary failed over on 2026-09-02, two instances both acquired the lock for `nightly-invoices`, and about 1,900 customers received their invoices twice.

We already run PostgreSQL 15 for the main application.

## Decision

The scheduler will lock jobs with PostgreSQL advisory locks instead of Redis. Before running a job, an instance calls `pg_try_advisory_lock(key)` with a key derived from the job name. If it does not get the lock, it skips the job.

## Alternatives considered

- **Redlock.** Rejected: it still relies on timing assumptions, and it would require at least three independent Redis nodes just for locking.
- **Make every job idempotent.** Agreed as the long-term fix, but it is about a quarter of work across 14 jobs, so it is not part of this decision.

## Consequences

- Locking no longer depends on Redis failover behaviour, and needs no new infrastructure.
- Advisory locks are session-scoped: if the scheduler's database connection drops mid-job, the lock is released and another instance could start the same job. This is unresolved.
- We have not measured how many advisory locks we would hold at peak.

## Open questions

- How do we prevent a second run when the connection holding the lock drops mid-job?
- What is the peak number of advisory locks, and is it acceptable for the database?
