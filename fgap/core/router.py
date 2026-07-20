import logging

import aiohttp
from aiohttp import web

from fgap.core.config import ConfigError
from fgap.core.credential import select_credential
from fgap.core.executor import execute_cli
from fgap.core.http import close_session, set_session
from fgap.plugins.base import Plugin

logger = logging.getLogger(__name__)

# gh subcommands that accept the -R/--repo flag for repository targeting.
# Injecting -R into any other subcommand makes gh exit with "unknown
# shorthand flag" before any API call — `api` targets via endpoint paths
# and `repo` takes the repository as a positional argument instead.
GH_REPO_FLAG_COMMANDS = frozenset({
    "browse",
    "cache",
    "codespace",
    "issue",
    "label",
    "pr",
    "release",
    "ruleset",
    "run",
    "search",
    "secret",
    "variable",
    "workflow",
})


def find_plugin_for_tool(tool: str, plugins: dict[str, Plugin]) -> Plugin | None:
    for plugin in plugins.values():
        if tool in plugin.tools:
            return plugin
    return None


def create_routes(config: dict, plugins: dict[str, Plugin]) -> web.Application:
    """Create aiohttp app with /cli and /health routes.

    Accepts plugins directly — use this in tests.
    """
    # Plugin-owned config validation, fail-fast at startup. A config
    # section for a plugin that is not loaded is an error (the grants
    # it describes would silently not be enforced otherwise).
    plugin_sections = config.get("plugins", {})
    for section_name in plugin_sections:
        if section_name not in plugins:
            raise ConfigError(
                f"Config has a 'plugins.{section_name}' section but no such "
                f"plugin is loaded"
            )
    for name, plugin in plugins.items():
        if name in plugin_sections:
            plugin.validate_config(plugin_sections[name])

    app = web.Application(client_max_size=0)

    # Shared HTTP session lifecycle
    timeouts = config.get("timeouts", {})
    http_timeout = timeouts.get("http", 30)
    cli_timeout = timeouts.get("cli")

    async def session_ctx(app):
        session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=http_timeout),
        )
        set_session(session)
        yield
        await close_session()

    app.cleanup_ctx.append(session_ctx)

    async def handle_cli(request: web.Request) -> web.Response:
        data = await request.json()

        tool = data.get("tool", "")
        args = data.get("args", [])
        resource = data.get("resource", "")
        stdin_data = data.get("stdin_data")
        cmd = args[0] if args else ""

        try:
            if not tool:
                raise web.HTTPBadRequest(text="Missing 'tool' field")

            is_help = any(a in ("--help", "-h") for a in args)

            if not resource:
                if not is_help:
                    raise web.HTTPBadRequest(text="Missing 'resource' field")
                resource = "_/help"

            # Find plugin
            plugin = find_plugin_for_tool(tool, plugins)
            if not plugin:
                raise web.HTTPBadRequest(text=f"No plugin handles tool: {tool}")

            plugin_config = config.get("plugins", {}).get(plugin.name, {})

            # Policy check: the plugin owns the judgment (service-specific
            # grammar), the config owns the grants, this is the choke point
            deny_reason = plugin.check_policy(args, resource, plugin_config)
            if deny_reason is not None:
                logger.info(
                    "cli tool=%s resource=%s policy denied: %s",
                    tool, resource, deny_reason,
                )
                raise web.HTTPForbidden(text=f"Policy denied: {deny_reason}")

            # Select credential, then resolve it into injectable env vars
            # (App credentials mint a short-lived token here); downstream
            # consumers always see the uniform {"env": {...}} shape
            credential = plugin.select_credential(resource, plugin_config)
            env = (await plugin.resolve_credential_env(credential, plugin_config)
                   if credential else None)
            if env is None:
                if not is_help:
                    raise web.HTTPForbidden(text=f"No credential for {tool} on {resource}")
                env = {}
            credential = {"env": env}

            # Try custom commands (with fallthrough)
            commands = plugin.get_commands()
            if cmd and cmd in commands:
                result = await commands[cmd](args[1:], resource, credential)
                if result is not None:
                    logger.info(
                        "cli tool=%s resource=%s cmd=%s exit_code=%d",
                        tool, resource, cmd, result["exit_code"],
                    )
                    return web.json_response(result)

            # Execute CLI subprocess
            # The wrapper strips -R/--repo for resource detection, so
            # re-inject it — but only for subcommands that accept the flag
            cli_args = args
            if tool == "gh" and cmd in GH_REPO_FLAG_COMMANDS:
                cli_args = args + ["-R", resource]
            result = await execute_cli(tool, cli_args, credential["env"], timeout=cli_timeout, stdin_data=stdin_data)
            logger.info(
                "cli tool=%s resource=%s cmd=%s exit_code=%d",
                tool, resource, cmd, result["exit_code"],
            )
            return web.json_response(result)

        except web.HTTPException as exc:
            logger.warning(
                "cli tool=%s resource=%s cmd=%s rejected=%d %s",
                tool, resource, cmd, exc.status_code, exc.reason,
            )
            raise

    async def handle_health(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def handle_auth_status(request: web.Request) -> web.Response:
        statuses = {}
        for name, plugin in plugins.items():
            plugin_config = config.get("plugins", {}).get(name, {})
            statuses[name] = await plugin.health_check(plugin_config)
        return web.json_response({"plugins": statuses})

    async def handle_download(request: web.Request) -> web.StreamResponse:
        """Proxy-authenticated file download.

        The client sends ``{tool, resource, url}``; the server selects a
        credential for *resource*, fetches *url* with that credential, and
        streams the bytes back.  Used by ``gh release download`` and
        similar commands that write files to local disk.
        """
        data = await request.json()

        tool = data.get("tool", "")
        resource = data.get("resource", "")
        url = data.get("url", "")

        try:
            if not tool or not resource or not url:
                raise web.HTTPBadRequest(
                    text="Missing required fields: tool, resource, url",
                )

            if not url.startswith("https://"):
                if not config.get("allow_insecure_download_urls", False):
                    raise web.HTTPBadRequest(
                        text="Only HTTPS URLs are allowed for downloads",
                    )

            plugin = find_plugin_for_tool(tool, plugins)
            if not plugin:
                raise web.HTTPBadRequest(
                    text=f"No plugin handles tool: {tool}",
                )

            plugin_config = config.get("plugins", {}).get(plugin.name, {})
            credential = plugin.select_credential(resource, plugin_config)
            env = (await plugin.resolve_credential_env(credential, plugin_config)
                   if credential else None)
            if not env:
                raise web.HTTPForbidden(
                    text=f"No credential for {tool} on {resource}",
                )

            token = env["GH_TOKEN"]
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/octet-stream",
                "User-Agent": "fgap",
            }

            session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=300),
            )
            try:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        raise web.HTTPBadGateway(
                            text=f"Upstream error: {resp.status} {text}",
                        )

                    response = web.StreamResponse(
                        status=200,
                        headers={
                            "Content-Type": resp.headers.get(
                                "Content-Type", "application/octet-stream",
                            ),
                        },
                    )
                    cl = resp.headers.get("Content-Length")
                    if cl:
                        response.headers["Content-Length"] = cl

                    await response.prepare(request)
                    async for chunk in resp.content.iter_any():
                        await response.write(chunk)
                    await response.write_eof()

                    logger.info(
                        "download tool=%s resource=%s url=%s",
                        tool, resource, url[:80],
                    )
                    return response
            finally:
                await session.close()

        except web.HTTPException as exc:
            logger.warning(
                "download tool=%s resource=%s rejected=%d %s",
                tool, resource, exc.status_code, exc.reason,
            )
            raise

    app.router.add_post("/cli", handle_cli)
    app.router.add_post("/download", handle_download)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/auth/status", handle_auth_status)

    # Plugin-specific routes (e.g. git smart HTTP proxy)
    for name, plugin in plugins.items():
        plugin_config = config.get("plugins", {}).get(name, {})
        for method, path, handler in plugin.get_routes(plugin_config):
            app.router.add_route(method, path, handler)

    return app


def create_app(config: dict) -> web.Application:
    """Create the full application with known plugins."""
    from fgap.plugins import discover_plugins, register_plugin

    # Register known plugins
    try:
        from fgap.plugins.github import GitHubPlugin
        register_plugin(GitHubPlugin)
    except (ImportError, ValueError):
        pass

    try:
        from fgap.plugins.google import GooglePlugin
        register_plugin(GooglePlugin)
    except (ImportError, ValueError):
        pass

    try:
        from fgap.plugins.notion import NotionPlugin
        register_plugin(NotionPlugin)
    except (ImportError, ValueError):
        pass

    try:
        from fgap.plugins.langfuse import LangfusePlugin
        register_plugin(LangfusePlugin)
    except (ImportError, ValueError):
        pass

    try:
        from fgap.plugins.fly import FlyPlugin
        register_plugin(FlyPlugin)
    except (ImportError, ValueError):
        pass

    try:
        from fgap.plugins.http_proxy import HttpProxyPlugin
        register_plugin(HttpProxyPlugin)
    except (ImportError, ValueError):
        pass

    try:
        from fgap.plugins.s3 import S3Plugin
        register_plugin(S3Plugin)
    except (ImportError, ValueError):
        pass

    plugins = discover_plugins(config)
    return create_routes(config, plugins)
