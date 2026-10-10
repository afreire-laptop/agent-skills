# RFC: Per-tenant rate limiting for the public API

Author: Dana Okafor · Status: Draft · Reviewers: API platform, SRE

## Summary

The API gateway has one global rate limit, so a single noisy tenant can exhaust it for everyone; this happened twice last month (INC-2240, INC-2263). This RFC proposes per-tenant limits enforced in the gateway with a token bucket per tenant, stored in Redis.

## Requirements

- The limiter MUST stay backward compatible with v1 clients: no new required request headers, and v1 response bodies keep their shape. v1 clients will see the new 429 responses, which they already handle from the global limiter.
- The limiter MUST NOT add more than 2 ms to p99 gateway latency. Each check is a single Redis round trip, implemented as a Lua script.

## Design

- Default limit: 600 requests per minute per tenant, with a burst of 100.
- Over the limit, the gateway returns 429 with a `Retry-After` header in seconds.
- If Redis is unreachable, the gateway fails open: it lets requests through and emits the `ratelimit.redis_unavailable` metric. Blocking all API traffic is worse than a noisy neighbour.

## Alternatives considered

- **Enforce limits in each service.** Rejected: six services would each implement it, and the implementations would drift.
- **The CDN's managed rate limiting.** Rejected: it can only key on IP address, not tenant.

## Risks

- The Redis cluster also holds user sessions; heavy limiter traffic could degrade logins. A dedicated cluster avoids this at extra cost, which has not been estimated yet.

## Rollout and rollback

1. Ship behind the `rate_limit_v2` flag in shadow mode for one week, logging what would have been limited.
2. Enforce for 10% of tenants.
3. Enforce for all tenants.

Rollback: turn the flag off.

## Non-goals

- Per-user limits.
- Per-endpoint limits.

## Open questions

- Enterprise contracts specify custom limits. Where should those overrides live: gateway config or the billing service?
