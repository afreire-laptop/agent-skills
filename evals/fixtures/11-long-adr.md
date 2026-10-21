# ADR 0014: Publish order events through a transactional outbox

Status: Proposed
Authors: Ines Duarte, Kwame Mensah
Last updated: 2026-10-09

## How we got here

This ADR started as a thread in #orders-platform on 2026-08-27, the day after INC-2318. At the first design meeting on 2026-09-03 we agreed the problem was the dual write and listed four options, including the outbox. On 2026-09-10 Kwame presented Debezium, and we spent most of that meeting on whether the platform team could take on Kafka Connect; they said no for this year. Ines ran the benchmark below on 2026-09-24, and at the meeting on 2026-10-01 everyone present agreed to propose the outbox. Since then we have only tidied the text.

## Context

`orders-api` writes an order to PostgreSQL and then publishes an `order.created` event to Kafka from the request handler. These are two separate writes, and nothing makes them succeed or fail together.

When the publish fails after the database commit, the order exists but no event goes out. In INC-2318 on 2026-08-26, a 9-minute Kafka broker outage lost 2,140 `order.created` events, and fulfilment never shipped those orders until support noticed three days later. The reverse also happens: when the database commit fails after a successful publish, fulfilment sees an order that doesn't exist. We counted 11 of these last quarter.

Any fix has to meet three constraints:

- It MUST NOT add more than 5 ms to the p99 latency of the checkout transaction.
- Events for the same order MUST be delivered in the order they were written.
- It SHOULD NOT need infrastructure that no team here already operates.

## Decision

`orders-api` will write each event to an `outbox` table in the same transaction as the order. A separate `outbox-relay` process reads unsent rows in insertion order, publishes them to Kafka, and marks them sent.

Delivery becomes at-least-once: a relay that crashes after publishing a row but before marking it sent publishes it again on restart.

## Alternatives considered

**Keep publishing from the request handler, with retries.** Retrying the publish narrows the window but doesn't close it. If the process dies between the commit and the publish, the event is still lost, and a Kafka outage longer than the retry budget loses events as before.

**Change data capture with Debezium.** Debezium reads the PostgreSQL write-ahead log and publishes changes, which also removes the dual write. We rejected it for two reasons. It needs Kafka Connect, which no team here operates. It also publishes row changes rather than domain events, so every consumer would have to rebuild `order.created` from row diffs.

**Kafka transactions.** Kafka's transactional producer makes writes atomic across Kafka partitions, but not across Kafka and PostgreSQL, so it doesn't solve this problem.

## Consequences

Good:

- An event is published if and only if its order commits, though not immediately.
- Per-order ordering holds, because the relay publishes in insertion order per order key.
- No new infrastructure: the relay is a deployment of the existing `orders-api` image with a different entrypoint.

Bad:

- Every consumer must be idempotent, because delivery is at-least-once. `fulfilment` already deduplicates on `event_id`; `billing` and `notifications` do not yet, and must before the switch.
- Events wait for the relay's poll interval, planned at 500 ms, where today they are published immediately.
- The `outbox` table grows by about 180,000 rows a day and needs a cleanup job.
- The relay is a new process to watch. If it stops, events queue up silently, so we need an alert on the age of the oldest unsent row.

## Benchmark

Ines ran this on 2026-09-24 against staging (db.r6g.xlarge, PostgreSQL 15.4): two 30-minute runs at 400 checkouts per second, one on the current code and one with the outbox write added.

| Metric | Current | With outbox |
|---|---|---|
| p50 checkout transaction | 6.1 ms | 6.4 ms |
| p99 checkout transaction | 18.2 ms | 19.9 ms |
| p99 outbox insert | – | 0.6 ms |
| p99 relay lag | – | 740 ms |

Raw output from the second run:

```
run=outbox-2 duration=1800s rate=400/s tx_p50=6.41ms tx_p99=19.93ms
insert_p99=0.61ms relay_lag_p50=212ms relay_lag_p99=741ms errors=0
rows_written=720112 rows_sent=720112 dup_publishes=0
```

The outbox adds 1.7 ms to the p99 of the checkout transaction, within the 5 ms budget.

## Implementation plan

1. Migration `20261014_create_outbox.sql` creates `outbox (id bigserial primary key, aggregate_id uuid not null, type text not null, payload jsonb not null, created_at timestamptz not null default now(), sent_at timestamptz)`, with a partial index on `created_at` where `sent_at is null`.
2. `OutboxWriter` in `orders-api/src/events/outbox.ts` replaces the direct calls to `producer.send` in the order handlers.
3. The relay in `orders-api/src/relay/main.ts` polls every 500 ms with `SELECT … WHERE sent_at IS NULL ORDER BY id LIMIT 500`, publishes the batch, and sets `sent_at`.
4. Deploy two relay replicas. Only the one holding a PostgreSQL advisory lock publishes; the other takes over if it stops.
5. Run in shadow mode for one week, with the relay publishing to `order.created.shadow` while the handler still publishes directly.
6. Switch `fulfilment`, `billing`, and `notifications` to the relay's events.
7. Remove the direct publish.
8. Add a nightly cleanup job at 03:00 UTC that deletes rows sent more than 7 days ago.

Tracked in ORD-411, ORD-412, ORD-413, ORD-415, and ORD-420.

## Open questions

- How long should sent rows be kept? The plan says 7 days, but the audit team has asked whether they need 90 days for payment disputes. Not decided.
- Should `payments` use the same outbox table or its own?

## Glossary

- **Outbox:** a table that holds events written in the same transaction as the data they describe, until a separate process publishes them.
- **Dual write:** writing the same change to two systems without a transaction that spans both.
- **At-least-once delivery:** every event is delivered, and some may be delivered more than once.
