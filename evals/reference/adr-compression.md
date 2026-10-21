# ADR 0014: Publish order events through a transactional outbox

Status: Proposed
Authors: Ines Duarte, Kwame Mensah

## Context

`orders-api` writes an order to PostgreSQL and then publishes an `order.created` event to Kafka from the request handler: two writes that nothing makes succeed or fail together. In INC-2318 on 2026-08-26, a 9-minute Kafka broker outage lost 2,140 `order.created` events, and those orders weren't shipped until support noticed three days later. The reverse also happens: last quarter, 11 events went out for orders whose commit then failed.

The fix:

- MUST NOT add more than 5 ms to the p99 latency of the checkout transaction.
- MUST deliver events for the same order in the order they were written.
- SHOULD NOT need infrastructure that no team here already operates.

## Decision

`orders-api` writes each event to an `outbox` table in the same transaction as the order. A separate `outbox-relay` process reads unsent rows in insertion order, publishes them to Kafka, and marks them sent. Delivery becomes at-least-once: a relay that crashes after publishing a row but before marking it sent publishes it again.

In a staging benchmark at 400 checkouts per second, the outbox added 1.7 ms to the p99 of the checkout transaction (18.2 ms to 19.9 ms), within the budget, with a p99 relay lag of 740 ms.

## Alternatives considered

- **Keep publishing from the handler, with retries.** Narrows the window but doesn't close it: an event is still lost if the process dies between commit and publish, or if a Kafka outage outlasts the retries.
- **Change data capture with Debezium.** Needs Kafka Connect, which no team here operates, and publishes row changes rather than domain events, so every consumer would rebuild `order.created` from row diffs.
- **Kafka transactions.** Atomic across Kafka partitions, not across Kafka and PostgreSQL.

## Consequences

- An event is published if and only if its order commits, and per-order ordering holds because the relay publishes in insertion order per order key.
- No new infrastructure: the relay runs the existing `orders-api` image with a different entrypoint.
- Every consumer must be idempotent. `fulfilment` deduplicates on `event_id`; `billing` and `notifications` don't yet, and must before the switch.
- Events wait for the relay's poll interval, planned at 500 ms, instead of being published immediately.
- The `outbox` table grows by about 180,000 rows a day and needs a cleanup job.
- If the relay stops, events queue up silently, so we need an alert on the age of the oldest unsent row.

## Open questions

- How long should sent rows be kept? The plan says 7 days; the audit team has asked whether payment disputes need 90.
- Should `payments` use the same outbox table or its own?
