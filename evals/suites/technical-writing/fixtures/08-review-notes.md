My notes from reviewing PR #614, `feat(webhooks): retry failed deliveries` by Priya. I spent most of the afternoon on it.

The parts of the diff the notes refer to:

```python
# webhooks/retry.py
 9  MAX_ATTEMPTS = 12
10  BASE_DELAY_S = 2
..
40  def next_delay(attempt: int) -> float:
41      # exponential backoff
42      return BASE_DELAY_S * 2 ** attempt
..
45  def schedule_retry(delivery: Delivery, response: Response) -> None:
46      delivery.attempt += 1
47      delay = next_delay(delivery.attempt)
..
55  def should_retry(status: int) -> bool:
56      if status >= 400:
57          return True
58      return False
```

```python
# webhooks/models.py
14  class Delivery(Base):
15      id = Column(Integer, primary_key=True)
16      attempt = Column(SmallInteger, default=0)
17      status = Column(String(16))
```

Notes:

- Backoff, retry.py:42. No cap and no jitter. My first thought was that the missing cap was the problem, so I worked out the delays: 2, 4, 8, and so on up to 4096 s on the 12th attempt, about 68 minutes. Long, but our guidelines allow up to 2 hours, so the cap is fine. The real problem is jitter. I wrote a quick script that queues 500 deliveries against a receiver that is down for 10 minutes, and when it came back, 480 of them retried within the same second. That's the same thundering herd as INC-1187 with the old cron sender. docs/webhooks.md#retries says retries MUST use full jitter. This has to be fixed before merge. Fix: `return random.uniform(0, BASE_DELAY_S * 2 ** attempt)`.
- should_retry, retry.py:56. It retries every 4xx. A 410 Gone means the customer deleted the endpoint, and a 400 won't succeed on a retry either. I grepped a day of staging logs: 31% of failed deliveries were 410s, and each of those would now be retried 12 times. Among the 4xx, only 408 and 429 are worth retrying. Also has to be fixed before merge, and tests/test_retry.py only covers 500 and 503, so it needs a 410 case (no retry) and a 429 case (retry). Fix:

  ```python
  return status >= 500 or status in (408, 429)
  ```

- Duplicate deliveries: in my local run some deliveries arrived twice, and I spent an hour on it thinking the idempotency key was regenerated on every attempt. It wasn't the PR. My local Redis was being flushed by another test run. Nothing to comment.
- Retry-After, retry.py:47. On 429 and 503 receivers often send Retry-After, and schedule_retry ignores it. Not blocking, but it would be nice to wait the Retry-After seconds when the header is present instead of next_delay().
- models.py:17 only keeps the last status. Support asked last quarter for the last error message on failed deliveries (SUP-332). Not blocking, a follow-up PR is fine. Something like `last_error = Column(Text, nullable=True)`, set when a delivery fails.
- retry.py:9, tiny nit: MAX_ATTEMPTS is hardcoded, but every other webhook setting is in settings.py (WEBHOOK_TIMEOUT_S and so on). Move it to settings.WEBHOOK_MAX_ATTEMPTS.
