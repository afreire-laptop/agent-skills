# 🚀 Understanding Our Cache Invalidation Strategy

In today's fast-paced world of distributed systems, caching isn't just a performance optimization — it's a fundamental pillar of a seamless user experience. Let's dive in and delve into how our robust, cutting-edge item cache works under the hood.

## ✨ The Big Picture

At its core, our caching strategy is built on three key principles: speed, consistency, and resilience. Every item is cached in the regional Redis cache with a TTL of 300 seconds. This ensures that frequently accessed data is always readily available, empowering our services to deliver lightning-fast responses.

## 🔄 How Writes Work

When an item is updated via `PUT /items/:id`, the API writes to the database and then updates the cache in the same region (write-through). But that's not all! The API also publishes an event to the `items.changed` Kafka topic. Other regions consume this event and invalidate their copy of the item.

- **Speed:** Writes are reflected immediately in the region that handled the request.
- **Consistency:** Other regions typically catch up in under 2 seconds, so a read in another region can return the old value for up to about 2 seconds.
- **Resilience:** If Kafka is unavailable, invalidation events are not delivered, and other regions fall back to the TTL — meaning they can serve stale data for up to 300 seconds.

## 💡 Why This Matters

It's important to note that this approach strikes the perfect balance between performance and correctness. By leveraging write-through caching alongside event-driven invalidation, we've unlocked a truly game-changing architecture that scales effortlessly.

## 🎯 Conclusion

In conclusion, our cache invalidation strategy is a testament to our commitment to excellence. It's not just about caching data — it's about building trust with our users. We hope this deep dive has been helpful!
