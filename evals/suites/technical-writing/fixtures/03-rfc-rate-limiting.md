# RFC: Per-tenant rate limiting for the public API

Author: Dana Okafor · Status: Draft · Reviewers: API platform, SRE

So the background here is that right now we have one global rate limit on the API gateway and a single noisy tenant can eat the whole budget, which happened twice last month (see INC-2240 and INC-2263). I've been thinking about this for a while and I think the answer is per-tenant limits enforced in the gateway using a token bucket per tenant, with the buckets stored in Redis. I looked at doing it in each service instead but that means six services each need to implement it and they'd drift, so gateway it is. We also looked at the managed rate limiting in our CDN but it can only key on IP address, not tenant, so it doesn't solve the problem.

The default limit would be 600 requests per minute per tenant with a burst of 100. When a tenant goes over, the gateway returns 429 with a `Retry-After` header in seconds. One really important thing is that this MUST stay backward compatible with v1 clients, so we can't require any new request headers, and existing v1 response bodies must not change shape — the 429 is new for them but they already handle 429 from the global limiter. Also SRE was very clear that the limiter MUST NOT add more than 2 ms to p99 gateway latency, so the Redis call has to be a single round trip (I'm planning a Lua script for that).

One thing that worries me a bit: the Redis cluster we'd use is the same one that holds user sessions. If the limiter hammers it we could hurt logins. We could get a dedicated cluster but that's extra cost (I don't have a number yet). If Redis is unreachable the gateway should fail open, i.e. let requests through and emit the `ratelimit.redis_unavailable` metric, because blocking all API traffic is worse than a noisy neighbour.

We're explicitly not doing per-user or per-endpoint limits in this RFC; that can come later. Rollout would be behind the `rate_limit_v2` flag, starting in shadow mode where we only log what would have been limited, for one week, then enforce for 10% of tenants, then everyone. Rollback is just turning the flag off.

Open question I haven't figured out: enterprise customers have contracts with custom limits, and I don't know yet where those overrides should live (gateway config vs the billing service).
