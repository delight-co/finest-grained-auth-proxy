import logging

import aiohttp
from aiohttp import web

from fgap.core.credential import select_credential
from fgap.core.executor import execute_cli
from fgap.core.http import close_session, set_session
from fgap.core.policy import evaluate
from fgap.plugins.base import Plugin

logger = logging.getLogger(__name__)


def find_plugin_for_tool(tool: str, plugins: dict[str, Plugin]) -> Plugin | None:
    for plugin in plugins.values():
        if tool in plugin.tools:
            return plugin
    return None


def create_routes(config: dict, plugins: dict[str, Plugin]) -> web.Application:
    """Create aiohttp app with /cli and /health routes.

    Accepts plugins directly — use this in tests.
    """
    app = web.Application()

    # Shared HTTP session lifecycle
    timeouts = config.get("timeouts", {})
    http_timeout = timeouts.get("http", 30)
    cli_timeout = timeouts.get("cli", 60)

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
        cmd = args[0] if args else ""

        try:
            if not tool:
                raise web.HTTPBadRequest(text="Missing 'tool' field")

            if not resource:
                raise web.HTTPBadRequest(text="Missing 'resource' field")

            # Find plugin
            plugin = find_plugin_for_tool(tool, plugins)
            if not plugin:
                raise web.HTTPBadRequest(text=f"No plugin handles tool: {tool}")

            # Policy check (allow-all stub)
            if not await evaluate(tool, cmd, resource, config):
                raise web.HTTPForbidden(text="Policy denied")

            # Select credential
            plugin_config = config.get("plugins", {}).get(plugin.name, {})
            credential = plugin.select_credential(resource, plugin_config)
            if not credential:
                raise web.HTTPForbidden(text=f"No credential for {tool} on {resource}")

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
            result = await execute_cli(tool, args, credential["env"], timeout=cli_timeout)
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

    app.router.add_post("/cli", handle_cli)
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

    plugins = discover_plugins(config)
    return create_routes(config, plugins)
