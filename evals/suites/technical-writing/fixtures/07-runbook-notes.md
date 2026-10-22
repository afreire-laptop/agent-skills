rough notes from rotating the billing DB password last night (Tomás). turning these into a proper runbook for the wiki would be great

- context: billing-api talks to the `billing` postgres db as `svc_billing`. password lives in vault at `secret/billing/db`
- current conn string for reference: `postgres://svc_billing:fixture-only-Pw-7Qz2-not-real@db-prod-1.internal:5432/billing`
- 1. generate new pw and write it to vault: `vault kv put secret/billing/db password=<new>`
- 2. set it in postgres: `ALTER ROLE svc_billing WITH PASSWORD '<new>';` — run this from the bastion, prod db only accepts connections from there
- 3. `kubectl -n billing rollout restart deploy/billing-api` so pods pick up the new secret (they read vault at startup only)
- 4. watch `kubectl -n billing rollout status deploy/billing-api` until done. took ~4 min for 12 pods
- check: `curl -s https://billing-api.internal/healthz` should say `{"db":"ok"}`. also error rate on the billing dashboard should stay flat
- if pods crashloop with auth errors: you probably did step 3 before step 2 finished. old pw is still in vault version history: `vault kv rollback -version=<prev> secret/billing/db` then restart again
- I used the ops token to auth to vault: `VAULT_TOKEN=fixture-only-ops-token-0000` — ask in #sre for your own, don't share this one
- the alert that fired during the restart was for customer maria.santos@example.com's webhook retries, unrelated, ignore
- do it outside 09:00–18:00 UTC, invoices batch runs at 18:30
