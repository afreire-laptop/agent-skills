## Getting started

logship runs on macOS and Linux and needs Node.js 20.11 or later; it refuses to start on older versions. Windows is not supported.

### Install

```sh
npm install -g logship@3
logship --version
```

### Configure

Create `~/.config/logship/config.toml`; logship does not create it for you. The only required setting is `endpoint`, the address logs are sent to. You can set it in the file or with the `LOGSHIP_ENDPOINT` environment variable, which takes precedence when both are set.

Logs are sent in batches of 500 lines by default; change this with `batch_size`. A batch never exceeds 10 MB, and larger batches are split automatically.

### Try it

Run with `--dry-run` first to see what logship would send without sending anything.

All configuration options are documented at https://docs.logship.example/config.
