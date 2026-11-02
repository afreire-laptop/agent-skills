# agent-skills

[![skills.sh](https://skills.sh/b/afreire-laptop/technical-writing)](https://skills.sh/afreire-laptop/technical-writing)

[Agent Skills](https://agentskills.io/specification) for engineering work.

## Skills

| Skill | What it does | Docs | Changelog |
|---|---|---|---|
| [`technical-writing`](skills/technical-writing/SKILL.md) | Writes and revises engineering documents that people read, such as READMEs, runbooks, ADRs, PR descriptions, and commit messages. Measured for Claude Code. | [docs](docs/technical-writing.md) | [changelog](skills/technical-writing/CHANGELOG.md) |

## Install

Install a skill with the [skills CLI](https://github.com/vercel-labs/skills), which needs Node.js 22.20.0 or later. Name the skill with `--skill` and the agent with `-a`:

```sh
npx skills add afreire-laptop/agent-skills --skill technical-writing -g -a claude-code
```

This copies the skill's directory to `~/.claude/skills/technical-writing`. Start a new session after installing.

- For one project, run the command in the repository without `-g`. The CLI copies the skill to `.claude/skills/<skill>` and records it in `skills-lock.json`.
- Without `-a`, the CLI installs into every agent it detects.
- If you list more than one agent, add `--copy`. With several agents, the CLI defaults to symlinks and keeps the main copy in `~/.agents/skills/`, which Codex and Pi also load.
- To update, run `npx skills update`. `-g` or `-p` limits it to global or project skills.

To pin a release, install from its tag. `update` keeps a pinned install on that tag; run `add` again to move it.

```sh
npx skills add https://github.com/afreire-laptop/agent-skills/tree/technical-writing-v0.4.0/skills/technical-writing -g -a claude-code
```

This repository was named `technical-writing` until 2026-10-22. Commands and installs that use the old name keep working through GitHub's redirect.

## Use

An agent loads a skill when a task matches its description. To invoke one explicitly:

| Agent | Command |
|---|---|
| Claude Code | `/<skill>` |
| Codex | `$<skill>`, or pick it from `/skills` |
| Pi | `/skill:<skill>` (needs `enableSkillCommands`) |

## Contributing

[CONTRIBUTING.md](CONTRIBUTING.md) covers evals, the repository layout, commit rules, and releases.

## License

[MIT](LICENSE)
