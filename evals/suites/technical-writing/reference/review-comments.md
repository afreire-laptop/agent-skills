`webhooks/retry.py:42`

Required: add full jitter to the backoff. docs/webhooks.md#retries says retries MUST use full jitter; without it, deliveries that failed during an outage retry together when the receiver recovers, the thundering herd from INC-1187.

```python
return random.uniform(0, BASE_DELAY_S * 2 ** attempt)
```

`webhooks/retry.py:56`

Required: retry only 5xx, 408, and 429. Other 4xx responses aren't worth retrying; a 410 means the customer deleted the endpoint, and each one is now retried 12 times. Please also add a 410 case (no retry) and a 429 case (retry) to `tests/test_retry.py`.

```python
return status >= 500 or status in (408, 429)
```

`webhooks/retry.py:47`

Suggestion: when a 429 or 503 response has a `Retry-After` header, wait that many seconds instead of `next_delay()`. The receiver is telling us when it can take traffic again. Not blocking.

`webhooks/models.py:17`

Suggestion, fine as a follow-up PR: store the last error message on failed deliveries, which support asked for in SUP-332. `status` keeps only the last status.

```python
last_error = Column(Text, nullable=True)
```

`webhooks/retry.py:9`

Nit: move `MAX_ATTEMPTS` to `settings.WEBHOOK_MAX_ATTEMPTS`, next to the other webhook settings such as `WEBHOOK_TIMEOUT_S`.
