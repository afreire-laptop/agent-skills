# Rotate the billing database password

Rotates the password of the `svc_billing` role, which `billing-api` uses to connect to the `billing` PostgreSQL database. The password is stored in Vault at `secret/billing/db`.

## Before you start

- Run the rotation outside 09:00–18:00 UTC. The invoice batch runs at 18:30 UTC.
- You need access to the bastion host: the production database accepts connections only from there.
- You need your own Vault token. Ask in #sre; do not use anyone else's.

## Steps

1. Generate a new password and write it to Vault:

   ```sh
   vault kv put secret/billing/db password=<new-password>
   ```

2. From the bastion, set the same password in PostgreSQL:

   ```sql
   ALTER ROLE svc_billing WITH PASSWORD '<new-password>';
   ```

   Wait for this to finish before restarting: pods read the secret from Vault only at startup.

3. Restart `billing-api` so the pods pick up the new secret:

   ```sh
   kubectl -n billing rollout restart deploy/billing-api
   ```

4. Watch the rollout until it completes (about 4 minutes for 12 pods):

   ```sh
   kubectl -n billing rollout status deploy/billing-api
   ```

## Verify

- `curl -s https://billing-api.internal/healthz` returns `{"db":"ok"}`.
- The error rate on the billing dashboard stays flat.

## Roll back

If pods crash-loop with authentication errors, the restart probably happened before step 2 finished. Restore the previous password from Vault's version history, then restart again:

```sh
vault kv rollback -version=<previous-version> secret/billing/db
kubectl -n billing rollout restart deploy/billing-api
```
