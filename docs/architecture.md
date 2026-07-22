# Architecture

What fgap is made of and the invariants that hold it together. Behavior details bound to specific code live in module docstrings (each plugin's `plugin.py`); this document covers the shapes that outlive any one plugin.

## Shape

```
Sandbox                      Proxy host
  client wrappers  ── HTTP ──▶ fgap ── credential injection ──▶ CLI subprocesses
  git / curl / S3 clients          ── auth header / SigV4 ────▶ upstream HTTP APIs
```

| Package | Responsibility |
|---|---|
| `fgap/core` | Config loading and validation (JSON5, owner-only permission check at startup), HTTP routing, async CLI executor, credential dispatch, secret/email masking, managed-process supervisor, shared HTTP clients |
| `fgap/plugins` | One package per service, each implementing the `Plugin` interface below |
| `fgap/client` | Thin Python wrappers (`fgap-gh`, `fgap-gog`, `fgap-notion`, `fgap-langfuse`, `fgap-fly`, `fgap-aws`): detect the resource, transform arguments (e.g. `--body-file` inlining), POST to `/cli` |

## HTTP surface

| Endpoint | Purpose |
|----------|---------|
| `POST /cli` | Execute a CLI command |
| `POST /download` | Proxy-authenticated file download (`gh release download` and similar file-streaming commands) |
| `GET /git/{owner}/{repo}.git/...` | Git smart HTTP proxy |
| `ANY /proxy/{service}/...` | Generic HTTP forward proxy with credential injection |
| `ANY /s3/{service}/{bucket}/{key}` | S3-compatible storage proxy with SigV4 re-signing |
| `GET /health` | Bare liveness (for Docker HEALTHCHECK) |
| `GET /auth/status` | Per-plugin credential health |
| `GET /processes` | Managed local processes status |

## Wire protocol (`/cli`)

```
Request:  { "tool": "gh", "args": ["issue", "list"], "resource": "acme-org/my-repo" }
Response: { "exit_code": 0, "stdout": "...", "stderr": "..." }
```

`tool` selects the plugin; `resource` selects the credential within it. The credential resolves to env vars injected into the proxy-side subprocess — it never travels back to the client.

**Custom command fallthrough**: plugin-registered commands get first shot at a `/cli` request; returning `None` falls through to the CLI subprocess. This is how `gh discussion`, `gh sub-issue`, `gh issue edit --old/--new`, and `gh issue close --duplicate-of` exist without forking `gh`.

## Credential selection

Credential arrays are evaluated top-to-bottom, **first match wins**, against resource patterns:

| Pattern | Matches |
|---------|---------|
| `owner/repo` | Exact match (case-insensitive) |
| `owner/*` | All repos of that owner |
| `*` | Everything (fallback) |

The same routing carries per-entry grants (permissions, service opt-ins), so a grant and the key it applies to can't drift apart — one entry declares both.

## Plugin interface

`fgap/plugins/base.py`:

| Member | Responsibility |
|---|---|
| `name` | Plugin identifier (`"github"`) |
| `tools` | CLI binaries handled (`["gh"]`); empty for route-only plugins (http_proxy, s3) |
| `select_credential(resource, config)` | Resource → credential entry (first-match-wins) |
| `resolve_credential_env(credential, config)` | Credential → env vars to inject; override for async work such as minting short-lived tokens (GitHub App installation tokens) |
| `get_routes(config)` | Custom HTTP routes (git smart HTTP, `/proxy`, `/s3`) |
| `get_commands()` | Custom `/cli` commands with fallthrough |
| `check_policy(args, resource, config)` | Allow, or return a human-readable deny reason (router turns it into HTTP 403) |
| `validate_config(config)` | Fail fast at startup on schema violations |
| `health_check(config)` | Status dicts for `/auth/status` |

## Permission architecture (three layers)

Whenever authorization matters beyond "have credential or not," fgap splits responsibility three ways:

- **Core** provides the choke point and the generic tools: it calls `Plugin.check_policy(args, resource, config)` before credential selection on every `/cli` request, turns a returned deny reason into `HTTP 403 Policy denied: <reason>`, and exposes `match_resource` / `check_keys` helpers so plugins don't reinvent them. Core never encodes what any specific service considers safe.
- **Plugin** owns the judgment logic. Permission grammar and granularity are service-specific: langfuse maps CLI verbs (`list`/`get` = read, `create`/`update`/`delete` = write, `__schema` = read); the aws plugin uses curated per-service `(service, operation)` tables because verb prefixes alone are unsafe (`ssm get-parameter` returns secrets, `logs start-query` is read-intent with a write-shaped verb, `ecr get-login-password` mints credentials). Unrecognized shapes are denied by default (allowlist, not denylist).
- **Config** holds the concrete grants. They ride the same first-match-wins `resources` routing the credential itself uses. langfuse: `permissions: ["read"]`. aws: `services: ["logs", "ecs"]`.

`Plugin.validate_config(config)` runs at app creation to enforce a strict schema per plugin: everything not explicitly optional is required, unknown keys are rejected, and a config section for a plugin that is not loaded is a startup error — a config that is missing something or contains something unrecognized is wrong either way, and the alternative is a runtime error with the credential unusable.

When a grammar-level read-only grant is paired with a read-only IAM principal (aws), grammar and principal fail independently — a grammar gap is caught by the principal, an over-granted principal is caught by the grammar.

## Credential flow invariant

The credential never enters the sandbox:

- `/cli` — env vars injected into the proxy-side subprocess
- `/git` — Basic auth attached proxy-side
- `/proxy` — Bearer / Basic / named header / OAuth2 token attached proxy-side
- `/s3` — dummy client signature stripped, request re-signed with the real keys proxy-side

The one deliberate exception: fly local-context commands (`deploy`, `logs`, `ssh`, ...) need the client's working directory or a live connection, so the proxy hands the app-scoped token to the client — and logs the handout. Fly's API refuses to let tokens mint ephemeral sub-tokens, which is why the exception exists at all; per-app tokens in config keep each handout app-scoped.

## History

fgap generalizes [fgp](https://github.com/carrotRakko/github-finest-grained-permission-proxy), which proved the pattern for GitHub only. The construction-time blueprint (fgp code analysis, migration map, size estimates) lived in `DESIGN.md` until 2026-07 and retired to git history when this document replaced it.
