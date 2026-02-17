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

## Quick Start

### 1. Create Config

```bash
cp config.example.json5 config.json5
# Edit config.json5 with your credentials
chmod 600 config.json5
```

See [config.example.json5](config.example.json5) for all options.

### 2. Start the Proxy

Requires [gh](https://cli.github.com/) and/or [gog](https://github.com/steipete/gogcli) installed on the host, depending on which plugins you use.

```bash
uv run python main.py --config config.json5
```

Default port: `8766`

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

## Endpoints

| Endpoint | Purpose |
|----------|---------|
| `POST /cli` | Execute a CLI command |
| `GET /git/{owner}/{repo}.git/...` | Git smart HTTP proxy |
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
| `gh search *` | Cross-repo search; `--repo` flag gets consumed by resource detection |
| `gh gist *` | User-scoped, no repo context |
| `gh status` | User dashboard (cross-repo) |
| `gh ssh-key *` / `gh gpg-key *` | User-scoped |
| `gh codespace *` | User-scoped |
| `gh api /user`, `/orgs/...`, `/search/...` | Non-`repos/` endpoints — no repo to match |
| `gh api graphql` | Raw GraphQL blocked — use high-level commands (issue, pr, discussion, sub-issue) |

Other limitations:

- **Git LFS not supported**: Only basic git smart HTTP protocol (clone/fetch/push)
- **Repo-scoped commands need context**: Either use `-R owner/repo`, or run from inside a git repo with a remote

## License

MIT
