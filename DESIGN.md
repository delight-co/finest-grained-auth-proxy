# DESIGN.md

## Overview

fgap (finest-grained-auth-proxy) is a multi-CLI auth proxy that isolates credentials from AI agent sandbox environments. It generalizes fgp (github-finest-grained-permission-proxy), which proved the pattern for GitHub-only use.

This document serves as the implementation blueprint. It contains:
1. Analysis of fgp's codebase (the source of truth for what works)
2. fgap's module structure and interfaces
3. Migration map from fgp to fgap

## 1. fgp Codebase Analysis

### File Structure

```
github-finest-grained-permission-proxy/
├── main.py                    # 23 lines. Entry point.
├── fgh                        # 708 lines. Bash thin wrapper (client).
├── install.sh                 # 48 lines. curl | bash installer.
├── fgp/
│   ├── __init__.py            # Version.
│   ├── server.py              # 72 lines. HTTPServer startup, argparse, mask_token.
│   ├── handler.py             # 348 lines. BaseHTTPRequestHandler: routing, /cli, /git/*, /auth/status.
│   └── core/
│       ├── __init__.py        # Re-exports.
│       ├── policy.py          # 571 lines. PAT selection, policy evaluation, endpoint→action mapping, config loading.
│       └── graphql.py         # 93 lines. GraphQL execution utilities.
│   └── commands/
│       ├── __init__.py        # 64 lines. Command registry, execute_command, get_cli_action.
│       ├── discussion.py      # 687 lines. Discussion GraphQL commands (list/view/create/edit/close/reopen/delete/comment/answer/poll).
│       ├── issue.py           # 209 lines. Issue/comment partial body replacement (--old/--new).
│       └── sub_issue.py       # 281 lines. Sub-issue GraphQL commands (list/parent/add/remove/reorder).
```

Total: ~2,900 lines of Python + 708 lines of Bash.

### Responsibility Map

| Component | Runs on | Responsibility |
|-----------|---------|---------------|
| **fgh** (Bash) | Sandbox (devcontainer) | Repo detection (git remote, -R flag, API endpoint parse). Argument transformation (--body-file inlining, --head auto-injection, --replace mode). Forwards everything to fgp `/cli`. |
| **fgp server.py** | Host | HTTPServer startup, CLI args, token masking for display. |
| **fgp handler.py** | Host | HTTP routing (/cli, /git/*, /auth/status). gh CLI subprocess execution. git smart HTTP proxying to github.com. |
| **fgp core/policy.py** | Host | PAT selection (resource pattern → token, first-match-wins). Policy evaluation (action × repo, IAM-style, but currently dead code). Endpoint → action mapping (93 REST endpoint patterns). Config loading + validation (JSON5, chmod 600 check). |
| **fgp core/graphql.py** | Host | GraphQL execution against GitHub API. Helper to resolve node IDs (repository, issue). |
| **fgp commands/** | Host | Custom commands that gh CLI doesn't have. Each module: ACTIONS list, get_action(), execute(). Returns None to fall through to gh CLI. |

### Key Design Patterns in fgp

**1. PAT selection is pure resource matching.**

```python
# policy.py: select_pat()
for pat_entry in config["pats"]:
    for repo_pattern in pat_entry["repos"]:
        if expand_repo_pattern(repo_pattern, repo):
            return pat_entry["token"]
```

First-match-wins over a `pats` array. Patterns: `"owner/*"`, `"owner/repo"`, `"*"`.

**2. Command module fallthrough.**

```python
# handler.py: handle_cli_request()
if cmd in COMMAND_MODULES:
    result = execute_command(cmd, args[1:], owner, repo_name, pat)
    if result is None:
        # Module declined; fall through to gh CLI
        result = self.execute_gh_cli(args, repo, pat)
```

Custom commands get first shot. If they return None, the request falls through to `gh` subprocess. This lets `issue.py` handle only `issue edit --old --new` while falling through for all other `issue` subcommands.

**3. CLI execution = subprocess with env injection.**

```python
# handler.py: execute_gh_cli()
result = subprocess.run(
    ["gh"] + args + ["-R", repo],
    env={**os.environ, "GH_TOKEN": pat, ...},
    capture_output=True, text=True, timeout=60,
)
return {"exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
```

The credential never reaches the sandbox. It's injected into the subprocess environment on the proxy side.

**4. git smart HTTP proxy = HTTP forwarding with auth header injection.**

```python
# handler.py: proxy_git_to_github()
credentials = base64.b64encode(f"x-access-token:{pat}".encode()).decode()
headers = {"Authorization": f"Basic {credentials}", ...}
# Forward to https://github.com/{owner}/{repo}.git/...
```

Handles `info/refs`, `git-upload-pack` (fetch/clone), `git-receive-pack` (push).

**5. Policy evaluation exists but is dead code.**

`evaluate_policy()` in policy.py implements full IAM-style allow/deny evaluation with 93 endpoint→action mappings and layer 1/2 action bundles. However, `handler.py` never calls it — the new `pats` config format dropped `rules`. fgap inherits this decision: **allow all, with a policy extension point for the future**.

**6. fgh does heavy lifting for repo detection.**

fgh (Bash, 708 lines) has three repo detection strategies:
- Parse `git remote get-url origin`
- Parse `-R`/`--repo` flag from args
- Parse API endpoint from `gh api repos/{owner}/{repo}/...`

Plus argument transformations:
- `--body-file` → read file, convert to `--body`
- Auto-inject `--head {owner}:{branch}` for `pr create`
- Special handling for `auth`, `discussion`, `sub-issue` subcommands (help display, argument validation)

### What fgp Got Right (Keep)

- Credential never enters sandbox
- CLI subprocess execution model
- Command module fallthrough pattern
- Wire protocol: `{args, repo} → {exit_code, stdout, stderr}`
- JSON5 config with chmod 600 check
- Custom commands for missing gh features (discussion, sub-issue, issue --old/--new)

### What fgp Got Wrong (Fix)

- **Single-threaded** (BaseHTTPRequestHandler) → async (aiohttp.web)
- **GitHub-specific everywhere** → plugin isolation
- **Bash thin wrapper** (708 lines, error handling limited to `set -e`) → Python
- **Dead code** (policy evaluation, endpoint→action tables) → clean up, keep extension point only
- **Sync HTTP** (urllib.request.urlopen) → async (aiohttp.ClientSession)

---

## 2. fgap Module Structure

### Directory Layout

```
finest-grained-auth-proxy/
├── CLAUDE.md
├── LICENSE
├── DESIGN.md                        # This file
├── README.md
├── pyproject.toml
├── main.py                          # Entry point
│
├── fgap/
│   ├── __init__.py
│   ├── server.py                    # aiohttp.web server startup + CLI args
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py                # Config loading + validation (JSON5, chmod 600)
│   │   ├── credential.py            # Credential selection engine (delegates to plugins)
│   │   ├── executor.py              # CLI execution engine (async subprocess)
│   │   ├── router.py                # HTTP request routing + /cli handler + /auth/status handler
│   │   └── policy.py                # Policy evaluation (allow all, extension point)
│   │
│   ├── plugins/
│   │   ├── __init__.py              # Plugin registry + discovery
│   │   ├── base.py                  # Plugin base class / interface
│   │   │
│   │   ├── github/
│   │   │   ├── __init__.py
│   │   │   ├── plugin.py            # GitHubPlugin(Plugin) — registers tools, credentials, routes, commands
│   │   │   ├── credential.py        # PAT selection (repo axis, first-match-wins, GH_TOKEN env injection)
│   │   │   ├── git_proxy.py         # git smart HTTP proxy (upload-pack/receive-pack)
│   │   │   ├── graphql.py           # GraphQL execution utilities (from fgp)
│   │   │   └── commands/
│   │   │       ├── __init__.py      # GitHub custom command registry
│   │   │       ├── discussion.py    # from fgp, async
│   │   │       ├── issue.py         # from fgp, async
│   │   │       └── sub_issue.py     # from fgp, async
│   │   │
│   │   └── google/
│   │       ├── __init__.py
│   │       ├── plugin.py            # GooglePlugin(Plugin) — gog
│   │       └── credential.py        # OAuth credential selection (account axis)
│   │
│   └── client/
│       ├── __init__.py
│       ├── base.py                  # Common: proxy communication, error formatting
│       ├── gh.py                    # gh wrapper (fgh rewrite in Python)
│       └── gog.py                   # gog wrapper
│
└── tests/                           # Mirrors fgap/ structure
```

### Plugin Interface

```python
# fgap/plugins/base.py

from abc import ABC, abstractmethod
from aiohttp import web


class Plugin(ABC):
    """Base class for tool plugins."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Plugin identifier. e.g. 'github', 'google'."""
        ...

    @property
    @abstractmethod
    def tools(self) -> list[str]:
        """CLI binaries this plugin handles. e.g. ['gh', 'git']."""
        ...

    @abstractmethod
    def select_credential(self, resource: str, config: dict) -> dict | None:
        """Select credential for the given resource.

        Args:
            resource: Resource identifier.
                GitHub: 'acme-org/my-repo' (owner/repo)
                Google: 'default' or account identifier
            config: Plugin-specific config section from config file.

        Returns:
            Credential dict with 'env' key (env vars to inject into subprocess),
            or None if no credential matches.

            Example (GitHub):
                {"env": {"GH_TOKEN": "ghp_xxx", "GH_HOST": "github.com"}}
            Example (Google):
                {"env": {"GOG_KEYRING_PASSWORD": "..."}}
        """
        ...

    def get_routes(self) -> list[tuple[str, str, callable]]:
        """Return custom HTTP routes this plugin provides.

        Returns:
            List of (method, path_pattern, handler) tuples.
            handler signature: async (request: web.Request) -> web.Response

        Example (GitHub git proxy):
            [
                ("GET",  "/git/{owner}/{repo}.git/{path:.*}", handle_git_get),
                ("POST", "/git/{owner}/{repo}.git/{path:.*}", handle_git_post),
            ]

        Default: no custom routes.
        """
        return []

    def get_commands(self) -> dict[str, callable]:
        """Return custom commands this plugin handles.

        Commands intercept /cli requests before CLI subprocess execution.
        Return None from the handler to fall through to CLI subprocess.

        Returns:
            Dict of {command_name: execute_fn}.
            execute_fn signature:
                async (args: list[str], owner: str, repo: str, credential: dict)
                    -> dict | None
                Returns {"exit_code": int, "stdout": str, "stderr": str}
                    or None to fall through.

        Example (GitHub):
            {
                "discussion": execute_discussion,
                "sub-issue": execute_sub_issue,
                "issue": execute_issue,  # only handles --old/--new, else falls through
            }

        Default: no custom commands.
        """
        return {}

    async def health_check(self, config: dict) -> list[dict]:
        """Check credential health.

        Returns:
            List of status dicts, one per credential.
            Each dict: {"valid": bool, "masked_token": str, ...}

        Default: empty list.
        """
        return []
```

### Wire Protocol

```
# Request: POST /cli
{
    "tool": "gh",                        # NEW: which CLI tool
    "args": ["issue", "list"],
    "resource": "acme-org/my-repo"       # RENAMED: "repo" → "resource"
}

# Response
{
    "exit_code": 0,
    "stdout": "...",
    "stderr": "..."
}
```

Changes from fgp:
- Added `tool` field — identifies which CLI binary to execute / which plugin to route to.
- Renamed `repo` → `resource` — GitHub uses owner/repo, Google uses account name. Generic term.

### Config Schema

```json5
{
  // Server
  "port": 8766,

  // Plugins
  "plugins": {
    "github": {
      "credentials": [
        // First-match-wins, same as fgp's pats array
        { "token": "github_pat_xxx", "resources": ["delight-co/*"] },
        { "token": "github_pat_yyy", "resources": ["carrotRakko/*"] },
        { "token": "ghp_zzz", "resources": ["*"] }
      ]
    },
    "google": {
      "credentials": [
        {
          "client_id": "...",
          "client_secret": "...",
          "refresh_token": "...",
          "resources": ["default"]
        }
      ]
    }
  }
}
```

Changes from fgp:
- `pats` → `plugins.github.credentials` — namespaced per plugin.
- `repos` → `resources` — generic term.
- `rules` removed — allow all. Future: `plugins.github.policy`.

### Core Implementation Notes

**config.py**: Load JSON5, validate chmod 600, validate plugin config sections. Each plugin can define its own validation (e.g., GitHub requires `token` + `resources`, Google requires `client_id` + `client_secret` + `refresh_token` + `resources`).

**credential.py**: Routes to the correct plugin based on `tool` field:

```python
async def select_credential(tool: str, resource: str, config: dict, plugins: dict[str, Plugin]) -> dict | None:
    for plugin in plugins.values():
        if tool in plugin.tools:
            plugin_config = config.get("plugins", {}).get(plugin.name, {})
            return plugin.select_credential(resource, plugin_config)
    return None
```

**executor.py**: Async subprocess wrapper:

```python
async def execute_cli(binary: str, args: list[str], env_overrides: dict, timeout: int = 60) -> dict:
    proc = await asyncio.create_subprocess_exec(
        binary, *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, **env_overrides},
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return {"exit_code": -1, "stdout": "", "stderr": f"Command timed out after {timeout}s"}
    return {
        "exit_code": proc.returncode,
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
    }
```

**router.py**: aiohttp.web request handler for `/cli`:

```python
async def handle_cli(request: web.Request) -> web.Response:
    data = await request.json()
    tool = data["tool"]
    args = data.get("args", [])
    resource = data["resource"]

    # 1. Find plugin for this tool
    plugin = find_plugin_for_tool(tool, plugins)
    if not plugin:
        raise web.HTTPBadRequest(text=f"No plugin handles tool: {tool}")

    # 2. Select credential
    plugin_config = config.get("plugins", {}).get(plugin.name, {})
    credential = plugin.select_credential(resource, plugin_config)
    if not credential:
        raise web.HTTPForbidden(text=f"No credential for {tool} on {resource}")

    # 3. Try custom commands (with fallthrough)
    commands = plugin.get_commands()
    cmd = args[0] if args else None
    if cmd and cmd in commands:
        result = await commands[cmd](args[1:], resource, credential)
        if result is not None:
            return web.json_response(result)

    # 4. Execute CLI subprocess
    result = await execute_cli(tool, args, credential["env"])
    return web.json_response(result)
```

**policy.py**: Stub for future use:

```python
async def evaluate(tool: str, action: str, resource: str, config: dict) -> bool:
    """Always returns True. Extension point for future policy enforcement."""
    return True
```

### Client (Thin Wrapper) Design

The client replaces fgh (708-line Bash). Written in Python.

**base.py**: Common proxy communication:

```python
class ProxyClient:
    def __init__(self, proxy_url: str):
        self.proxy_url = proxy_url

    def call(self, tool: str, args: list[str], resource: str) -> dict:
        """Send request to proxy and return result."""
        # POST to {proxy_url}/cli
        # Handle connection errors, timeouts
        # Return {"exit_code", "stdout", "stderr"}
```

**gh.py**: GitHub-specific wrapper. Responsibilities migrated from fgh:
- Resource detection: `git remote get-url origin` parse, `-R` flag parse, API endpoint parse
- Argument transformation: `--body-file` → `--body`, `--head` auto-injection for `pr create`
- Subcommand routing: `auth`, `discussion`, `sub-issue` help display
- `--replace` mode: act as `gh` binary

**gog.py**: Google-specific wrapper. Simpler (100-200 lines estimated):
- Resource detection: default account or config-based
- Minimal argument transformation

---

## 3. Migration Map

What goes where when porting fgp code to fgap.

| fgp file | Lines | fgap destination | Migration strategy |
|----------|-------|------------------|--------------------|
| `main.py` | 23 | `main.py` | Nearly identical |
| `server.py` | 72 | `fgap/server.py` | Replace HTTPServer with aiohttp.web. Keep argparse, mask_token |
| `handler.py` `/cli` handler | ~60 | `fgap/core/router.py` | Add `tool` field routing, delegate to plugin, async |
| `handler.py` `execute_gh_cli` | ~25 | `fgap/core/executor.py` | Generalize (tool as param), async subprocess |
| `handler.py` `/git/*` handler | ~70 | `fgap/plugins/github/git_proxy.py` | Nearly same, async HTTP (aiohttp.ClientSession) |
| `handler.py` `/auth/status` | ~90 | `fgap/core/router.py` + `Plugin.health_check()` | Delegate to plugins |
| `core/policy.py` `select_pat` | ~30 | `fgap/plugins/github/credential.py` | Nearly same, rename repo→resource |
| `core/policy.py` `load_config` | ~80 | `fgap/core/config.py` | Plugin namespace, remove legacy format |
| `core/policy.py` endpoint tables | ~250 | **Delete** | Dead code (allow all). Future: `plugins/github/policy.py` |
| `core/policy.py` `evaluate_policy` | ~50 | `fgap/core/policy.py` (stub) | Stub that returns True |
| `core/graphql.py` | 93 | `fgap/plugins/github/graphql.py` | Nearly same, async |
| `commands/__init__.py` | 64 | `fgap/plugins/github/commands/__init__.py` | Same pattern, scoped to GitHub plugin |
| `commands/discussion.py` | 687 | `fgap/plugins/github/commands/discussion.py` | Same logic, async GraphQL calls |
| `commands/issue.py` | 209 | `fgap/plugins/github/commands/issue.py` | Same logic, async REST calls |
| `commands/sub_issue.py` | 281 | `fgap/plugins/github/commands/sub_issue.py` | Same logic, async GraphQL calls |
| `fgh` (Bash) | 708 | `fgap/client/gh.py` | Full rewrite in Python |

### What's New (Not in fgp)

| fgap file | Purpose |
|-----------|---------|
| `fgap/plugins/base.py` | Plugin interface (ABC) |
| `fgap/plugins/__init__.py` | Plugin registry + discovery |
| `fgap/plugins/google/` | Google (gog) plugin |
| `fgap/client/base.py` | Shared proxy communication |
| `fgap/client/gog.py` | gog thin wrapper |
| `fgap/core/policy.py` | Stub (allow all) |

### Estimated Sizes

| Component | Estimated lines | Notes |
|-----------|----------------|-------|
| Core (server, config, credential, executor, router, policy) | ~300 | Mostly routing + delegation |
| Plugin base + registry | ~100 | ABC + discovery |
| GitHub plugin (credential, git_proxy, graphql, commands) | ~1,200 | Bulk is migrated commands |
| Google plugin | ~100 | Simpler credential model |
| Client base | ~80 | HTTP POST + error handling |
| Client gh.py | ~400 | Rewrite from 708 Bash → cleaner Python |
| Client gog.py | ~100 | Simple wrapper |
| **Total** | **~2,300** | vs fgp's ~2,900 Python + 708 Bash |

Smaller despite more features, because:
- Policy evaluation dead code removed (~250 lines)
- Bash → Python eliminates verbose error handling and argument parsing
- aiohttp.web eliminates boilerplate HTTP method handlers (do_GET, do_POST, ...)
