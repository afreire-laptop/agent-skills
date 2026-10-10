# Item cache invalidation

Items are cached in each region's Redis with a TTL of 300 seconds. A read in the region that handled the latest write sees the new value immediately; other regions can serve the old value for about 2 seconds, or for up to 300 seconds when Kafka is unavailable.

## Writes

When an item is updated through `PUT /items/:id`, the API writes to the database and then updates the cache in the same region (write-through). It also publishes an event to the `items.changed` Kafka topic. Other regions consume the event and invalidate their copy of the item.

## Staleness

- In the region that handled the write, reads reflect the write immediately.
- Other regions usually process the event in under 2 seconds, so a read there can return the old value for up to about 2 seconds.
- If Kafka is unavailable, other regions do not receive invalidation events and rely on the TTL, so they can serve stale data for up to 300 seconds.
