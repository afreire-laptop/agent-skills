```
feat(config)!: rename timeout to timeoutMs and use milliseconds

The request timeout is now configured in milliseconds through `timeoutMs`
(default 30000), which is passed to the HTTP client unchanged. The old
`timeout` key, in seconds, is removed without a fallback.

BREAKING CHANGE: the `timeout` config key is removed. Rename it to
`timeoutMs` and multiply the value by 1000. Configs that still set
`timeout` fail schema validation at startup.

Refs: #317
```
