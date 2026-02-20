# CLAUDE.md

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

## Development

This project uses **uv** for dependency management.

**Do NOT use `uv pip install` or `pip install`.** Instead:

```bash
uv sync --extra dev   # Install all dependencies (including test tools)
uv run pytest         # Run tests
```

If a command fails with "module not found", check `pyproject.toml` for the correct optional-dependency group and run `uv sync` — never install packages manually.
