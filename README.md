# finest-grained-auth-proxy (fgap)

> **0.x — Unstable**: Early development. APIs and config format may change without notice.

A multi-CLI auth proxy that isolates credentials from AI agent sandbox environments.

Successor to [fgp](https://github.com/carrotRakko/github-finest-grained-permission-proxy) (GitHub-only). fgap supports multiple CLI tools and HTTP upstreams via a plugin system.

## How It Works

```
Sandbox (fgap-* CLI wrappers · git · stock curl / S3 clients)
    | HTTP
Proxy server (fgap)
    | credential injection
CLI tools (gh, gog, notion, langfuse, flyctl, aws)
  · github.com (git smart HTTP) · upstream HTTP APIs · S3-compatible storage
```

- CLI wrappers (`fgap-gh`, `fgap-gog`, `fgap-notion`, `fgap-langfuse`, `fgap-fly`, `fgap-aws`) replace their CLIs inside the sandbox
- git, `curl`, and stock S3 clients need no wrapper — they point at proxy URLs (`/git/...`, `/proxy/...`, `/s3/...`)
- The proxy selects the appropriate credential based on resource patterns
- Credentials never enter the sandbox environment

## Supported Tools

| Plugin | CLI | Capabilities |
|--------|-----|-------------|
| GitHub | `gh` | Issues, PRs, REST API, discussions, sub-issues, git clone/fetch/push |
| Google | `gog` | Gmail, Calendar, Sheets, Docs, Drive, Contacts |
| Fly.io | `fly` / `flyctl` | App management via proxy-side flyctl; deploy/logs/ssh via logged per-app token handout |
| AWS | `fgap-aws` | Read-only observability (CloudWatch logs / metrics, ECS, ECR) via a curated allowlist; secret-bearing reads and credential minting denied |
| Langfuse | `langfuse` | LLM tracing / prompts via per-project API keys; per-entry `read` / `write` permission grammar enforced at the proxy |
| Notion | `notion` | Notion API via [notion-cli](https://github.com/4ier/notion-cli); the Internal Integration Token stays proxy-side |
| HTTP | stock `curl` | Generic forward proxy for authenticated HTTP APIs (`bearer` / `basic` / `header` / `oauth2` auth); passes MCP Streamable HTTP traffic. SSE responses (`text/event-stream`) are relayed without buffering — set `"streaming": true` on SSE/LLM services |
| S3 | stock `aws` / `rclone` | S3-compatible storage (AWS S3, Cloudflare R2, MinIO) via SigV4 re-signing; bucket allow-list, deletion deny, immutable puts |

The proxy also supervises **managed local processes** (typically stdio MCP servers behind a stdio-to-HTTP bridge) so their API keys stay on the host — see [docs/operations.md](docs/operations.md).

## Quick Start

### 1. Create config (proxy host)

```bash
cp config.example.json5 config.json5
# Fill in your credentials
chmod 600 config.json5
```

[config.example.json5](config.example.json5) is the source of truth for the config shape — every plugin and option, commented.

### 2. Start the proxy (proxy host)

```bash
uv run python main.py --config config.json5
```

Default port: `8766`. The proxy shells out to the real CLIs, so install the ones your config enables: [gh](https://cli.github.com/), [gog](https://github.com/steipete/gogcli), [flyctl](https://fly.io/docs/flyctl/), [aws](https://docs.aws.amazon.com/cli/), langfuse, [notion](https://github.com/4ier/notion-cli); the `http_proxy` and `s3` plugins need none. Daemon mode and the rest of the runbook: [docs/operations.md](docs/operations.md).

### 3. Install wrappers (sandbox side)

```bash
curl -fsSL https://raw.githubusercontent.com/delight-co/finest-grained-auth-proxy/main/install.sh | bash

# Or additionally replace gh/gog with symlinks to their wrappers
curl -fsSL https://raw.githubusercontent.com/delight-co/finest-grained-auth-proxy/main/install.sh | bash -s -- --replace
```

Per-tool usage, wrapper aliasing, and behavior notes: [docs/usage.md](docs/usage.md).

## Documentation

| Document | Covers |
|---|---|
| [docs/usage.md](docs/usage.md) | Sandbox side: installing wrappers, per-tool usage and limits |
| [docs/operations.md](docs/operations.md) | Proxy host: daemon mode, health/status endpoints, static bearer token files (`claude setup-token`), OAuth2 login + provider known-goods, GitHub App credentials, managed processes |
| [docs/architecture.md](docs/architecture.md) | Design: components, endpoint surface, wire protocol, credential selection, plugin interface, permission model |
| [config.example.json5](config.example.json5) | Config shape — every plugin and option, commented |
| [AGENTS.md](AGENTS.md) | Conventions for AI agents working on this repo, including the documentation source-of-truth map |

## Security

- Credentials stay on the proxy side, invisible to the sandbox
- Config file must be `chmod 600` — checked at startup
- Audit logging: CLI invocations logged with tool, resource, exit code; secrets masked in logs, emails masked in `/auth/status`
- **Local network only**: the proxy has no authentication of its own — do not expose it to the internet

## License

MIT
