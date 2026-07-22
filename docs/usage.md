# Usage (sandbox side)

How to call proxied tools from inside a sandbox. Host-side setup lives in [operations.md](operations.md); the config shape lives in [config.example.json5](../config.example.json5).

## Installing the wrappers

```bash
# Install the fgap package (ships all wrappers as entry points)
curl -fsSL https://raw.githubusercontent.com/delight-co/finest-grained-auth-proxy/main/install.sh | bash

# Or additionally replace gh/gog with symlinks to their wrappers
curl -fsSL https://raw.githubusercontent.com/delight-co/finest-grained-auth-proxy/main/install.sh | bash -s -- --replace
```

The package install provides `fgap-gh`, `fgap-gog`, `fgap-notion`, `fgap-langfuse`, `fgap-fly`, `fgap-aws` (and the host-side `fgap-oauth-login`). `--replace` symlinks `gh` and `gog` only; to shadow the other CLIs, add symlinks the same way (e.g. `sudo ln -s "$(command -v fgap-fly)" /usr/local/bin/fly`).

Set the proxy URL if not on localhost:

```bash
export FGAP_PROXY_URL=http://fgap:8766
```

## gh

```bash
gh issue list -R owner/repo
gh pr view 123 -R owner/repo
gh api repos/owner/repo/issues
gh auth status          # answered from /auth/status, not the GitHub API
```

### Custom commands (not in stock gh)

```bash
gh discussion list -R owner/repo
gh discussion create -R owner/repo --title "..." --body "..." --category "General"
gh sub-issue list 123 -R owner/repo
gh sub-issue add 100 200 -R owner/repo
gh issue edit 123 --old "typo" --new "fixed" -R owner/repo
gh issue close 123 --duplicate-of 456 -R owner/repo
```

### Behavior notes and limits

Every invocation runs under a credential selected by resource: the client resolves it from `-R owner/repo`, a repo positional, the API endpoint path, or the cwd's git remote — and refuses with "Could not determine repository" when none is found.

- User-scoped commands (`gh gist`, `gh status`, `gh api /user`, `/orgs/...`, ...) are not blocked — they execute under whichever credential the detected resource selects. Results reflect that token's identity and grants (a fine-grained PAT scoped to repos typically lacks user-scoped permissions), so pick the repo to pick the credential.
- `gh api graphql` is deliberately blocked by the client — use the high-level commands (issue, pr, discussion, sub-issue) instead.
- `gh auth *` subcommands that would leak the injected credential (`auth token`, `auth status --show-token`, `auth setup-git`) are denied at the proxy choke point; `fgap-gh auth status` (which queries `/auth/status`) is unaffected.
- **`gh search *` is single-repo**: the wrapper consumes `--repo` for credential selection and re-injects it as a single `-R`, so cross-repo searches (no `--repo`, or multiple `--repo` flags) collapse to one repository.
- **`repo` positionals must come right after the subcommand**: `gh repo view owner/repo --json name` selects the credential for `owner/repo`; with flags first, the wrapper falls back to the cwd's git remote. `owner/repo` and URL forms are accepted.
- **Bare `repo` subcommands other than `view`** (e.g. `gh repo clone` with no argument) aren't supported — the proxy server has no local git context.
- **Git LFS**: the proxy forwards LFS batch requests. Fine-grained PATs have been observed to work in some setups; if you hit LFS auth failures, a GitHub App credential is the known-reliable path (see [operations.md](operations.md)).

## git

```bash
# Clone via proxy
git clone http://fgap-host:8766/git/owner/repo.git

# Existing repos: change remote
git remote set-url origin http://fgap-host:8766/git/owner/repo.git
```

## gog

```bash
gog gmail search 'newer_than:7d'
gog calendar events primary
gog sheets get SHEET_ID 'Tab!A1:D10'
gog auth list
```

## notion

```bash
fgap-notion <command> [args...]   # commands: search / page / database / block / user / comment
```

Arguments are forwarded to the [notion CLI](https://github.com/4ier/notion-cli) on the proxy host; the Internal Integration Token is injected there. `fgap-notion --help` lists the commands.

## langfuse

```bash
fgap-langfuse --project my-project-prod <command> [args...]
```

All arguments are forwarded to the langfuse CLI on the proxy host. Langfuse API keys are per-project: `--project` (or `FGAP_LANGFUSE_PROJECT`) selects which configured credential the proxy uses. It must match a `resources` pattern of a credential entry — there is no default. Each entry also carries a `permissions` grant (`read` / `write`) enforced at the proxy.

## fly

```bash
fgap-fly status -a my-app
fgap-fly deploy            # local-context command, see below
```

API commands run on the proxy host with the credential injected there. Commands that need the local working directory or a live connection (`deploy`, `logs`, `ssh`, ...) run the local flyctl with the app's token handed out by the proxy — the handout is logged proxy-side. The target app is taken from `-a`/`--app`, then `$FLY_APP`, then `./fly.toml`.

## aws (read-only observability)

```bash
fgap-aws auth list                                             # accounts, identity, granted services
fgap-aws --account my-account logs tail /my/log-group --since 10m
fgap-aws --account my-account ecs describe-services --cluster c --services s
fgap-aws --account my-account cloudwatch list-metrics --namespace ECS/ContainerInsights
```

Only a curated read-only set per service is allowed (`logs`, `ecs`, `cloudwatch`, `ecr`). Denials name the reason: write operations, unsupported services (`ssm`, `secretsmanager`, ...), credential minting (`ecr get-login-password`), `--follow` streams, and `--profile` / `--endpoint-url` / `--debug` are all rejected at the proxy. The grant is account-wide per service — if multiple workloads share the account, reads span all of them. Pair the proxy-side credential with a read-only IAM principal ([aws-readonly-policy.example.json](../aws-readonly-policy.example.json)) so the guarantee holds in two independent layers.

## HTTP APIs (stock curl)

```bash
curl $FGAP_PROXY_URL/proxy/<service>/<path>
curl -X POST $FGAP_PROXY_URL/proxy/some_mcp/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

The proxy injects the service's credential (`bearer` / `basic` / arbitrary header / OAuth2) and forwards. MCP Streamable HTTP traffic passes through, and `text/event-stream` responses are relayed without buffering. Services with multiple credentials select by `?_resource=<name>` (stripped before forwarding). Full behavior reference: the `http_proxy` [plugin docstring](../fgap/plugins/http_proxy/plugin.py).

## S3-compatible storage (stock clients)

No wrapper needed — point a stock S3 client at the proxy with dummy credentials. The proxy strips the dummy signature, enforces policy (bucket allow-list, deletion deny, immutable puts), re-signs with the real credentials, and streams to the upstream (AWS S3, Cloudflare R2, MinIO, ...).

`~/.aws/credentials` (values are placeholders on purpose — the real keys live on the proxy side):

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
