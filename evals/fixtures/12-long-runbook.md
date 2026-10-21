# Orders database regional failover

Owner: Storage SRE (Wen Li). Last reviewed 2026-07-14.

## About this runbook

This runbook replaces the old `failover.sh` script, which we retired in March after it failed halfway through during INC-1902 and left the database in a state that took 47 minutes to untangle by hand. Since then we have done the failover manually, step by step, so that a person checks each stage before moving on. We have run it twice in production and four times in game days, and every run since we added the fencing step has gone cleanly. It looks long, but most of it is explanation, and the procedure itself takes about 4 minutes.

## Background: how the orders database is replicated

The orders database runs as a PostgreSQL primary in us-east with a streaming replica in eu-west. The primary ships its write-ahead log to the replica continuously, and the replica replays it, usually a few hundred milliseconds behind. The replica is read-only until it is promoted. When it is promoted, it stops replaying and starts accepting writes, and from then on it is the primary.

The important thing to understand is that promotion is one-way. Once the eu-west replica has been promoted, the old us-east primary can't simply rejoin, because the two have diverged. Getting back to us-east is a separate, slower procedure, the fail-back, described in `docs/runbooks/orders-failback.md`, which needs a planned maintenance window.

It is equally important that the old primary is fenced before promotion. Fencing cuts it off from clients, so that if it comes back while you are failing over, it can't accept writes. If you promote the replica while the old primary can still accept writes, you get split brain: two primaries, both taking orders, and the data has to be reconciled by hand.

## When to use this runbook

Use it when the us-east primary has been unreachable for more than 5 minutes, or when the incident commander decides to fail over for another reason. Don't use it for a slow primary or a short network blip; those usually recover on their own, and a failover is more disruptive than waiting.

Before you start, make sure you have:

- VPN access and the `ops-admin` role in `opsctl`.
- The incident commander's go-ahead, recorded in the incident channel.
- Posted in #incidents that orders writes are about to pause.

Writes to orders are paused for about 4 minutes during the failover. Reads keep working throughout.

## Procedure

First, put the orders service into maintenance mode, so that it stops accepting writes while you work. Customers see a "try again in a few minutes" message at checkout.

```sh
opsctl orders maintenance on
```

Next, check how far the eu-west replica is behind. This matters because anything the replica hasn't replayed yet is lost when you promote it.

```sh
psql "host=orders-db.eu-west.internal dbname=orders" -c "SELECT now() - pg_last_xact_replay_timestamp() AS lag;"
```

If the lag is under 30 seconds, carry on. If it is 30 seconds or more, stop and tell the incident commander, who decides whether to accept losing that much data. Either way, write the lag value in the incident timeline, because finance will need it to reconcile orders afterwards.

Now fence the old primary. Don't skip this step, even if the primary looks completely dead; this is the step that prevents split brain.

```sh
opsctl orders db fence --region us-east
```

With the old primary fenced, promote the eu-west replica:

```sh
opsctl orders db promote --region eu-west
```

Then point `orders-api` at the new primary and wait for the rollout to finish. The rollout usually takes about 2 minutes.

```sh
kubectl -n orders set env deploy/orders-api DB_HOST=orders-db.eu-west.internal
kubectl -n orders rollout status deploy/orders-api
```

Finally, take the service out of maintenance mode:

```sh
opsctl orders maintenance off
```

## Checking that it worked

The health endpoint should report the database as healthy and the region as eu-west:

```sh
curl -s https://orders.internal/healthz
```

The expected response is `{"db":"ok","region":"eu-west"}`. Also watch the "Orders / Writes" dashboard: the write rate should return to its usual level within 5 minutes.

## If something goes wrong

If you need to stop before you have promoted the replica, nothing has changed yet, so run `opsctl orders maintenance off` and the service carries on against us-east.

After promotion there is no rollback. Stay on eu-west, and fail back later with `docs/runbooks/orders-failback.md` in a planned maintenance window.

## FAQ

**Why isn't failover automatic?** We tried automatic failover in 2024, and it failed over twice during network blips that would have recovered on their own, each time causing a 3-minute write outage for nothing. A person deciding is slower but avoids that.

**Why eu-west and not another region?** It's the only region with a replica. A second replica in ap-south is planned for next year.

**Who do I ask if I'm stuck?** Storage SRE on-call, through the `@storage-oncall` handle in Slack.
