# finest-grained-auth-proxy (fgap)

> **0.x — Unstable**: Early development. APIs and config format may change without notice.

A multi-CLI auth proxy that isolates credentials from AI agent sandbox environments.

Successor to [fgp](https://github.com/carrotRakko/github-finest-grained-permission-proxy) (GitHub-only). fgap supports multiple CLI tools via a plugin system.

## How It Works

```
Sandbox (fgap-gh / fgap-gog wrappers)
    | HTTP
Proxy server (fgap)
    | credential injection
CLI tools (gh, gog) / GitHub API / github.com (git)
```

- Wrappers replace `gh` and `gog` inside the sandbox
- The proxy selects the appropriate credential based on resource patterns
- Credentials never enter the sandbox environment

## Supported Tools

| Plugin | CLI | Capabilities |
|--------|-----|-------------|
| GitHub | `gh` | Issues, PRs, REST API, discussions, sub-issues, git clone/fetch/push |
| Google | `gog` | Gmail, Calendar, Sheets, Docs, Drive, Contacts |
| Fly.io | `fly` / `flyctl` | App management via proxy-side flyctl; deploy/logs/ssh via logged per-app token handout |
| S3 | stock `aws` / `rclone` | S3-compatible storage (AWS S3, Cloudflare R2, MinIO) via SigV4 re-signing; bucket allow-list, deletion deny, immutable puts |

## Quick Start

### 1. Create Config

```bash
cp config.example.json5 config.json5
# Edit config.json5 with your credentials
chmod 600 config.json5
```

See [config.example.json5](config.example.json5) for all options.

### 2. Start the Proxy

Requires [gh](https://cli.github.com/), [gog](https://github.com/steipete/gogcli) and/or [flyctl](https://fly.io/docs/flyctl/) installed on the host, depending on which plugins you use.

```bash
uv run python main.py --config config.json5
```

Default port: `8766`

#### Background mode

```bash
uv run python main.py --config config.json5 \
  --daemon --pidfile /tmp/fgap.pid --logfile /tmp/fgap.log

# Stop
kill $(cat /tmp/fgap.pid)
```

`--logfile` is required with `--daemon`. `--pidfile` and `--logfile` also work in foreground mode.

### 3. Install Wrappers (Sandbox Side)

```bash
# Install fgap-gh and fgap-gog
curl -fsSL https://raw.githubusercontent.com/delight-co/finest-grained-auth-proxy/main/install.sh | bash

# Or replace gh/gog entirely
curl -fsSL https://raw.githubusercontent.com/delight-co/finest-grained-auth-proxy/main/install.sh | bash -s -- --replace
```

Set the proxy URL if not on localhost:

```bash
export FGAP_PROXY_URL=http://fgap:8766
```

## Usage (From Sandbox)

### gh commands

```bash
gh issue list -R owner/repo
gh pr view 123 -R owner/repo
gh api repos/owner/repo/issues
gh auth status
```

### git operations

```bash
# Clone via proxy
git clone http://fgap-host:8766/git/owner/repo.git

# Existing repos: change remote
git remote set-url origin http://fgap-host:8766/git/owner/repo.git
```

### gog commands

```bash
gog gmail search 'newer_than:7d'
gog calendar events primary
gog sheets get SHEET_ID 'Tab!A1:D10'
gog auth list
```

### Custom commands (not in stock gh)

```bash
gh discussion list -R owner/repo
gh discussion create -R owner/repo --title "..." --body "..." --category "General"
gh sub-issue list 123 -R owner/repo
gh sub-issue add 100 200 -R owner/repo
gh issue edit 123 --old "typo" --new "fixed" -R owner/repo
```

### S3-compatible storage

No wrapper needed — point a stock S3 client at the proxy with dummy
credentials. The proxy strips the dummy signature, enforces policy
(bucket allow-list, deletion deny, immutable puts), re-signs with the
real credentials, and streams to the upstream (AWS S3, Cloudflare R2,
MinIO, ...).

`~/.aws/credentials` (values are placeholders on purpose — the real
keys live on the proxy side):

```ini
[media]
aws_access_key_id = dummy
aws_secret_access_key = dummy
```

`~/.aws/config`:

```ini
[profile media]
region = auto
endpoint_url = http://fgap-host:8766/s3/media
request_checksum_calculation = when_required
response_checksum_validation = when_required
s3 =
    addressing_style = path
```

```bash
aws s3 cp video.mp4 s3://my-bucket/team/project/video.mp4 --profile media
aws s3 ls s3://my-bucket/team/ --recursive --profile media
aws s3 cp s3://my-bucket/team/project/video.mp4 ./video.mp4 --profile media
```

## Config Reference

```json5
{
  "port": 8766,
  "timeouts": {
    "cli": 60,       // CLI subprocess timeout (seconds)
    "http": 30        // Outbound HTTP timeout (seconds)
  },
  "plugins": {
    "github": {
      // Credentials evaluated top-to-bottom, first match wins
      "credentials": [
        { "token": "github_pat_ORG",      "resources": ["your-org/*"] },
        { "token": "github_pat_PERSONAL", "resources": ["your-username/*"] },
        { "token": "ghp_CLASSIC",         "resources": ["some-org/repo"] },
        // GitHub App credential: short-lived installation tokens are
        // minted (and cached) automatically from the App's private key
        {
          "app_id": 123456,
          "installation_id": 12345678,
          "private_key_path": "/path/to/github-app.pem",
          "repositories": "matched",              // optional narrowing
          "permissions": { "contents": "write" }, // optional narrowing
          "resources": ["your-org/*"]
        },
        { "token": "github_pat_FALLBACK", "resources": ["*"] }
      ]
    },
    "google": {
      "credentials": [
        {
          "keyring_password": "...",  // gog keyring password
          "account": "user@...",      // Optional: Google account
          "resources": ["*"]          // Resource patterns
        }
      ]
    }
  }
}
```

### Resource Patterns

| Pattern | Matches |
|---------|---------|
| `owner/repo` | Exact match (case-insensitive) |
| `owner/*` | All repos of that owner |
| `*` | Everything (fallback) |

Credentials are evaluated top-to-bottom. First match wins.

### GitHub App Credentials

Instead of a PAT, a credential can reference a GitHub App. fgap signs a
short-lived JWT with the App's private key, mints an installation access
token (valid one hour), caches it, and re-mints before expiry — callers
always see a fresh token, and the only long-lived secret is the key file.

Why you might want this over a fine-grained PAT:

- **Git LFS, as a safety net.** GitHub's LFS batch API used to reject fine-grained PATs (a long-standing platform limitation) — that's why App credentials were added. We've recently observed fine-grained PATs working for LFS in some setups: the proxy forwards the batch request, GitHub returns 200, and LFS objects download via a signed S3 URL that bypasses the proxy entirely. We don't fully understand why this works now (possibly a platform change). If you hit LFS failures with a fine-grained PAT, an App credential is the known-reliable fix.
- **Narrowing at mint time.** `"repositories": "matched"` scopes every
  minted token to the single repository that matched the credential's
  resource patterns; a `"permissions"` map caps token permissions below
  what the App is allowed. One App can serve many differently-scoped
  credentials.
- **No owner, no expiry surprises.** Tokens don't belong to a person and
  the key doesn't expire; revocation and audit happen at the App level.

Setup: create a GitHub App (only the permissions you need, webhook off),
install it on the repositories you want to expose, note the App ID and
the installation ID (the number at the end of the installation's URL),
generate a private key, and point `private_key_path` at it.

## Endpoints

| Endpoint | Purpose |
|----------|---------|
| `POST /cli` | Execute a CLI command |
| `GET /git/{owner}/{repo}.git/...` | Git smart HTTP proxy |
| `ANY /proxy/{service}/...` | Generic HTTP forward proxy with credential injection |
| `ANY /s3/{service}/{bucket}/{key}` | S3-compatible storage proxy with SigV4 re-signing |
| `GET /health` | Lightweight health check (for Docker HEALTHCHECK) |
| `GET /auth/status` | Credential validity check (for debugging) |

## Security

- Credentials stay on the proxy side, invisible to the sandbox
- Config file should be `chmod 600` (readable only by owner)
- Audit logging: all CLI invocations are logged with tool, resource, and exit code
- Credential masking: secrets are replaced with `***` in all log output
- Email addresses in `/auth/status` responses are masked
- **Local network only**: This proxy has no authentication. Do not expose to the internet.

## Limitations

All commands require a resource (owner/repo) to select the right credential. Commands that aren't repo-scoped won't work:

| Blocked command | Reason |
|----------------|--------|
| `gh gist *` | User-scoped, no repo context |
| `gh status` | User dashboard (cross-repo) |
| `gh ssh-key *` / `gh gpg-key *` | User-scoped |
| `gh codespace *` | User-scoped |
| `gh api /user`, `/orgs/...`, `/search/...` | Non-`repos/` endpoints — no repo to match |
| `gh api graphql` | Raw GraphQL blocked — use high-level commands (issue, pr, discussion, sub-issue) |

Other limitations:

- **Git LFS**: the proxy forwards LFS batch requests. Fine-grained PATs have been observed to work in some setups (see GitHub App Credentials above), but this isn't fully understood and may not hold everywhere. If you see LFS auth failures, switch to a GitHub App credential — that's the known-reliable path.
- **Repo-scoped commands need context**: Use `-R owner/repo` (or a repository positional for `repo` subcommands), or run from inside a git repo with a remote
- **`gh search *` is single-repo**: The wrapper consumes `--repo` for credential selection and re-injects it as a single `-R`, so cross-repo searches (no `--repo`, or multiple `--repo` flags) collapse to one repository
- **`repo` positionals must come right after the subcommand**: `gh repo view owner/repo --json name` selects the credential for `owner/repo`; with flags first (`gh repo view --json name owner/repo`) the wrapper falls back to the cwd's git remote for credential selection. `owner/repo` and URL forms (`https://github.com/owner/repo`, `git@github.com:owner/repo.git`) are accepted
- **Bare `repo` subcommands other than `view`**: `gh repo view` without an argument targets the cwd's remote; other bare invocations (e.g. `gh repo clone` with no argument) aren't supported because the proxy server has no local git context

## License

MIT
