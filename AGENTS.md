# AGENTS.md

> AGENTS.md is the canonical instruction file for AI agents working in this repo; `CLAUDE.md` is a symlink to it (Claude Code auto-loads CLAUDE.md). Reading either is enough. **Edit AGENTS.md** — tools refuse writes through the symlink.

## This is a public repository

Everything in this repo is visible to the world. Act accordingly.

## Language

All external communication must be in English:
- Commit messages
- Issue: title, body, comments
- PR: title, body, comments, review comments
- Code comments

## Information Hygiene

This repo is public. Never write any of the following:

- Private repository names or issue numbers (e.g. `some-org/private-repo#42`)
- Slack IDs (channel, user, timestamp)
- Session URLs, internal architecture details
- Internal project names or codenames

Org names (`delight-co`) and public user names (`carrotRakko`, `iku-min`) are fine.

**Scope**: commit messages, PR body, branch names, code comments, documentation — everything.

**GitHub keeps history**: PR body edits leave edit history. Force-pushed commits leave dangling SHAs. The only safe strategy is **never writing it in the first place**.

## Signature

Use the public-facing format for all commits, PRs, issues, and comments:

```
✍️ Author: Claude Code with @{GitHub username of the human} (AI-written, human-approved)
```

Do NOT use internal formats that reference specific environments or real names.

## Documentation map (source of truth)

Each kind of information has exactly one home. Fix things at their home and point (link) from everywhere else — never copy prose between documents.

| Information | Home |
|---|---|
| What fgap is, quick start | [README.md](README.md) |
| Config shape — every plugin and option | [config.example.json5](config.example.json5) (self-describing comments) |
| Sandbox-side usage — installing wrappers, per-tool commands and limits | [docs/usage.md](docs/usage.md) |
| Host-side operations — daemon mode, health endpoints, token files, OAuth2 login, managed processes | [docs/operations.md](docs/operations.md) |
| Design — components, plugin interface, wire protocol, permission model, endpoint surface | [docs/architecture.md](docs/architecture.md) |
| Behavior details bound to code | module docstrings (each plugin's `plugin.py`) |

Docs follow code, strictly:

- Never document behavior that isn't implemented.
- A PR that changes behavior or config shape updates the home document (and `config.example.json5` for new options) **in the same PR**.
- Field-verify claims before writing them (endpoints, command behavior); a wrong doc is worse than a missing one.

## Development

This project uses **uv** for dependency management.

**Do NOT use `uv pip install` or `pip install`.** Instead:

```bash
uv sync --extra dev   # Install all dependencies (including test tools)
uv run pytest         # Run tests
```

If a command fails with "module not found", check `pyproject.toml` for the correct optional-dependency group and run `uv sync` — never install packages manually.
