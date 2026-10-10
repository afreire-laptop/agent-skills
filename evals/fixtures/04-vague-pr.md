Current PR title: "fixes"

Current PR description:

> Fixed stuff and some refactors, should be good. Closes #482

Branch: `fix/export-timezone` → `main`

`git diff --stat main...HEAD`:

```
 db/migrations/20260930_add_tz_to_exports.sql |  9 +++++
 src/exports/csv.ts                            | 41 +++++++++++------
 src/exports/schedule.ts                       | 23 ++++++----
 src/exports/__tests__/csv.test.ts             | 58 ++++++++++++++++++++++++
 src/util/dates.ts                             | 30 ++++---------
 5 files changed, 117 insertions(+), 44 deletions(-)
```

Key hunks:

```diff
--- a/src/exports/csv.ts
+++ b/src/exports/csv.ts
-  const ts = format(row.createdAt, "yyyy-MM-dd HH:mm");
+  // Render in the account's timezone instead of the server's (UTC).
+  const ts = formatInTimeZone(row.createdAt, account.timezone ?? "UTC", "yyyy-MM-dd HH:mm");
```

```diff
--- a/src/exports/schedule.ts
+++ b/src/exports/schedule.ts
-  const runAt = startOfDay(new Date());
+  // Daily exports now run at local midnight for the account.
+  const runAt = nextLocalMidnight(account.timezone ?? "UTC");
```

```sql
-- db/migrations/20260930_add_tz_to_exports.sql
ALTER TABLE accounts ADD COLUMN timezone TEXT NULL;
```

```diff
--- a/src/util/dates.ts
+++ b/src/util/dates.ts
-export function toLocal(d: Date, offsetHours: number) { ... }   // removed, unused after this change
```

Author's notes (from Slack):

- Bug from #482: CSV exports showed UTC times, so customers in e.g. Australia saw yesterday's date on today's orders.
- `accounts.timezone` is nullable; null means we keep the old UTC behaviour, so nothing changes for accounts until they set a timezone.
- Unit tests pass, added cases for UTC, Australia/Sydney, and a DST transition day.
- I didn't run the integration suite, it's been flaky all week.
- Tested the migration against the staging dump. Haven't tried it on the MySQL 5.7 replica that the reporting team still reads from — not sure if it matters since the column is nullable.
- The `toLocal` cleanup in dates.ts is unrelated, just dead code I noticed.
