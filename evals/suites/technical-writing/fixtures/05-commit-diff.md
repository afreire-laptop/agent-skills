`commitlint.config.js` in the repository:

```js
module.exports = { extends: ["@commitlint/config-conventional"] };
```

`git log --oneline -6`:

```
3f2a9c1 fix(client): retry on 503 responses from the token endpoint
91be004 feat(cli): add --profile flag to select a named credentials profile
c0d7e55 docs: document proxy settings
7a1f3d2 refactor(config): load settings through a single parser
e44b190 test(client): cover timeout edge cases
b2c8a60 chore(deps): bump undici to 6.21.0
```

`git diff --cached`:

```diff
diff --git a/src/config/schema.ts b/src/config/schema.ts
--- a/src/config/schema.ts
+++ b/src/config/schema.ts
@@ -12,7 +12,7 @@ export const ConfigSchema = z.object({
   baseUrl: z.string().url(),
-  timeout: z.number().int().positive().default(30),
+  timeoutMs: z.number().int().positive().default(30_000),
   retries: z.number().int().min(0).max(10).default(3),
 });
diff --git a/src/client/http.ts b/src/client/http.ts
--- a/src/client/http.ts
+++ b/src/client/http.ts
@@ -40,7 +40,7 @@ export function createClient(config: Config) {
   return new HttpClient({
     baseUrl: config.baseUrl,
-    timeout: config.timeout * 1000,
+    timeout: config.timeoutMs,
     retries: config.retries,
   });
 }
diff --git a/docs/configuration.md b/docs/configuration.md
--- a/docs/configuration.md
+++ b/docs/configuration.md
@@ -20,7 +20,7 @@
-| `timeout` | number | `30` | Request timeout in seconds. |
+| `timeoutMs` | number | `30000` | Request timeout in milliseconds. |
diff --git a/CHANGELOG.md b/CHANGELOG.md
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -3,6 +3,10 @@
 ## Unreleased
+
+### Breaking
+
+- Renamed the `timeout` config key to `timeoutMs`; the value is now in milliseconds. Configs that still set `timeout` fail validation at startup.
```

Context from the developer: the old `timeout` key is removed, not deprecated. There is no fallback, so existing config files that set `timeout` will fail schema validation on startup. Issue: #317.
