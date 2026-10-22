# Orders database regional failover

Owner: Storage SRE (Wen Li). Last reviewed 2026-07-14.

Promotes the eu-west replica of the orders database to primary when the us-east primary is down. Use it when the primary has been unreachable for more than 5 minutes, or when the incident commander decides to fail over. Don't use it for a slow primary or a short network blip.

Orders writes pause for about 4 minutes; reads keep working. **Promotion is one-way:** after step 4 there is no rollback.

## Before you start

- VPN access and the `ops-admin` role in `opsctl`.
- The incident commander's go-ahead, recorded in the incident channel.
- A post in #incidents that orders writes are about to pause.

## Steps

1. Stop writes:

   ```sh
   opsctl orders maintenance on
   ```

2. Check the replica's lag. Anything it hasn't replayed is lost on promotion.

   ```sh
   psql "host=orders-db.eu-west.internal dbname=orders" -c "SELECT now() - pg_last_xact_replay_timestamp() AS lag;"
   ```

   Under 30 seconds, carry on. At 30 seconds or more, stop and let the incident commander decide. Either way, record the lag in the incident timeline; finance needs it to reconcile orders.

3. Fence the old primary, even if it looks dead. Without fencing, a returning primary keeps taking writes and you get split brain.

   ```sh
   opsctl orders db fence --region us-east
   ```

4. Promote the replica:

   ```sh
   opsctl orders db promote --region eu-west
   ```

5. Point `orders-api` at it and wait for the rollout, about 2 minutes:

   ```sh
   kubectl -n orders set env deploy/orders-api DB_HOST=orders-db.eu-west.internal
   kubectl -n orders rollout status deploy/orders-api
   ```

6. Resume writes:

   ```sh
   opsctl orders maintenance off
   ```

## Verify

- `curl -s https://orders.internal/healthz` returns `{"db":"ok","region":"eu-west"}`.
- The write rate on the "Orders / Writes" dashboard returns to normal within 5 minutes.

## If something goes wrong

- Before step 4, nothing has changed: run `opsctl orders maintenance off` to carry on against us-east.
- After step 4, stay on eu-west. Fail back later with `docs/runbooks/orders-failback.md`, in a planned maintenance window.
- If you're stuck, ask Storage SRE on-call: `@storage-oncall` in Slack.
