# Operations (proxy host)

Running and maintaining the proxy. Sandbox-side usage lives in [usage.md](usage.md); the config shape lives in [config.example.json5](../config.example.json5).

## Starting the proxy

```bash
cp config.example.json5 config.json5
# Fill in your credentials
chmod 600 config.json5

uv run python main.py --config config.json5
```

Default port: `8766`. The proxy shells out to the real CLIs of the plugins you configure — install those on the host (see the [README](../README.md#quick-start)).

### Background mode

```bash
uv run python main.py --config config.json5 \
  --daemon --pidfile /tmp/fgap.pid --logfile /tmp/fgap.log

# Stop
kill $(cat /tmp/fgap.pid)
```

`--logfile` is required with `--daemon`. `--pidfile` and `--logfile` also work in foreground mode.

## Health and status

| Endpoint | Answers |
|---|---|
| `GET /health` | Bare liveness (`{"status": "ok"}`) — for Docker HEALTHCHECK and "is it up" |
| `GET /auth/status` | Per-plugin credential health: identities (emails masked), `token_file` readability, configured services |
| `GET /processes` | Managed local processes status |

`fgap-gh auth status` from the sandbox is answered from `/auth/status`.

## Static bearer tokens from a file (`token_file`)

An `http_proxy` credential can name a file to read the token from instead of inlining it:

```json5
"credentials": [
  { "token_file": "~/.config/fgap/tokens/anthropic-bearer.txt", "resources": ["*"] }
]
```

The file holds the token as a single line (surrounding whitespace is trimmed; keep it owner-only, `chmod 600`). It is re-read on **every request**, so writing a new token to the file rotates the credential with no restart. `token` and `token_file` are mutually exclusive per credential, and `token_file` works with the `bearer`, `basic`, and `header` auth modes. `GET /auth/status` reports whether each configured token file currently yields a token (`/health` is the bare liveness probe).

Failure modes: a configured-but-missing or empty file answers with an actionable 502 that tells the operator what to fix. A `401` from the upstream means the token itself has expired or been revoked — static tokens have no refresh machinery, the proxy passes the upstream response through — so mint a new token and overwrite the file.

### Anthropic with a Claude subscription (`claude setup-token`)

The simplest way to front the Claude API for a coding-agent sandbox: mint a long-lived token with Claude Code's own `claude setup-token` (requires a Claude subscription), save it to the token file, and configure a static bearer service:

```json5
"anthropic": {
  "upstream": "https://api.anthropic.com",
  "auth": "bearer",
  "streaming": true,
  "forward_request_headers": ["anthropic-version", "anthropic-beta"],
  "append_headers": { "anthropic-beta": "oauth-2025-04-20" },
  "credentials": [
    { "token_file": "~/.config/fgap/tokens/anthropic-bearer.txt", "resources": ["*"] }
  ]
}
```

Setup on the proxy host:

```bash
claude setup-token   # sign in with the subscription that should hold the token
mkdir -p ~/.config/fgap/tokens
# paste the printed sk-ant-oat01-… token into the file:
printf '%s\n' 'sk-ant-oat01-REPLACE_ME' > ~/.config/fgap/tokens/anthropic-bearer.txt
chmod 600 ~/.config/fgap/tokens/anthropic-bearer.txt
```

Restart the proxy once to pick up the config change; afterwards, swapping the token (e.g. switching to another subscription: rerun `claude setup-token` under the other account) is just overwriting the file — no restart.

Compared with the `oauth2` route below, this keeps the whole OAuth dance inside first-party tooling — no authorize-URL / scope / state / User-Agent specifics to maintain, and no refresh windows to coordinate around. Tokens minted by `claude setup-token` carry the `user:inference` scope only: regular API and agent traffic (tool use, streaming, prompt caching) all works, while Claude Code features that demand a full login token (e.g. remote control, cloud review sessions) do not run through such a token.

## Interactive OAuth2 login (fgap-oauth-login)

For `http_proxy` services configured with `auth: oauth2`, the proxy needs a seeded token pair to refresh from. Run the login command on the **proxy host** (where a browser lives) once per service:

```bash
uv sync
uv run fgap-oauth-login --config <path/to/config.json5> --service <service_name>
```

Flow:

1. The command prints an authorization URL and opens it in your default browser. Sign in with the account whose subscription/organization should hold the token.
2. After consent, the provider shows an authorization code (possibly as `code#state`). Paste it into the prompt; the state is verified.
3. The command exchanges the code at the token endpoint and writes `<state_dir>/<service_name>.json` (owner-only). The proxy refreshes from this file thereafter.

### Provider-specific known-good configs

**Anthropic (`api.anthropic.com`)** — for fronting the Claude API in front of a coding-agent sandbox. With a Claude subscription, prefer the `claude setup-token` + `token_file` setup above (none of the wire-level pitfalls below apply to it); the oauth2 route remains for setups that need it:

```json5
"anthropic": {
  "upstream": "https://api.anthropic.com",
  "auth": "oauth2",
  "streaming": true,
  "forward_request_headers": ["anthropic-version", "anthropic-beta"],
  "append_headers": { "anthropic-beta": "oauth-2025-04-20" },
  "oauth2": {
    "token_url": "https://platform.claude.com/v1/oauth/token",
    "client_id": "9d1c250a-e61b-44d9-88ed-5944d1962f5e",
    "token_request_format": "json",
    "login": {
      "authorize_url": "https://claude.com/cai/oauth/authorize",
      "redirect_uri": "https://platform.claude.com/oauth/code/callback",
      "scope": "org:create_api_key user:profile user:inference user:sessions:claude_code user:mcp_servers user:file_upload",
      "extra_authorize_params": { "code": "true" }
    }
  }
}
```

The wire-level details, verified in the field 2026-07-22:

- The `authorize_url` is `claude.com/cai/…`, not `claude.ai/…` or `platform.claude.com/…`. The `platform.claude.com` authorize endpoint serves the org login flow and rejects individual-subscription users.
- The `scope` on the *authorize* request must include `org:create_api_key` and `user:file_upload` even though the issued token's scope set is smaller — the authorize server rejects requests missing them.
- The `state` parameter is 32 bytes of entropy (this is what `fgap-oauth-login` generates); anything shorter is rejected with "Invalid request format".
- The token endpoint sits behind a CDN that blocks the default Python-urllib User-Agent (Cloudflare error 1010). `fgap-oauth-login` and the refresh path both send an explicit UA.

## GitHub App credentials

Instead of a PAT, a `github` credential can reference a GitHub App. fgap signs a short-lived JWT with the App's private key, mints an installation access token (valid one hour), caches it, and re-mints before expiry — callers always see a fresh token, and the only long-lived secret is the key file.

Why you might want this over a fine-grained PAT:

- **Git LFS, as a safety net.** GitHub's LFS batch API used to reject fine-grained PATs; fine-grained PATs have recently been observed to work in some setups, but an App credential is the known-reliable path if you hit LFS auth failures.
- **Narrowing at mint time.** `"repositories": "matched"` scopes every minted token to the single repository that matched the credential's resource patterns; a `"permissions"` map caps token permissions below what the App is allowed. One App can serve many differently-scoped credentials.
- **No owner, no expiry surprises.** Tokens don't belong to a person and the key doesn't expire; revocation and audit happen at the App level.

Setup: create a GitHub App (only the permissions you need, webhook off), install it on the repositories you want to expose, note the App ID and the installation ID (the number at the end of the installation's URL), generate a private key, and point `private_key_path` at it. Config shape: [config.example.json5](../config.example.json5).

## Managed local processes

`managed_processes` entries are helpers the proxy spawns on startup, restarts on crash (exponential backoff), and terminates on shutdown — typically stdio MCP servers wrapped by a stdio-to-HTTP bridge, with their API keys in env on the proxy host. Pair each with an `http_proxy` service whose upstream is the local port, so the sandbox reaches the helper through the proxy and never sees the credential. Status: `GET /processes`. Config shape: [config.example.json5](../config.example.json5).

## Security posture

- Credentials stay on the proxy side, invisible to the sandbox.
- Config file should be `chmod 600` (readable only by owner); the proxy checks this at startup.
- Audit logging: all CLI invocations are logged with tool, resource, and exit code.
- Credential masking: secrets are replaced with `***` in all log output; email addresses in `/auth/status` responses are masked.
- **Local network only**: the proxy has no authentication of its own. Do not expose it to the internet.
