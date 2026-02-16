import logging

from aiohttp import web

from fgap.core.credential import select_credential
from fgap.core.executor import execute_cli
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

    async def handle_cli(request: web.Request) -> web.Response:
        data = await request.json()

        tool = data.get("tool")
        if not tool:
            raise web.HTTPBadRequest(text="Missing 'tool' field")

        args = data.get("args", [])

        resource = data.get("resource")
        if not resource:
            raise web.HTTPBadRequest(text="Missing 'resource' field")

        # Find plugin
        plugin = find_plugin_for_tool(tool, plugins)
        if not plugin:
            raise web.HTTPBadRequest(text=f"No plugin handles tool: {tool}")

        # Policy check (allow-all stub)
        cmd = args[0] if args else ""
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
                return web.json_response(result)

        # Execute CLI subprocess
        result = await execute_cli(tool, args, credential["env"])
        return web.json_response(result)

    async def handle_health(request: web.Request) -> web.Response:
        statuses = {}
        for name, plugin in plugins.items():
            plugin_config = config.get("plugins", {}).get(name, {})
            statuses[name] = await plugin.health_check(plugin_config)
        return web.json_response({"status": "ok", "plugins": statuses})

    app.router.add_post("/cli", handle_cli)
    app.router.add_get("/health", handle_health)

    # Plugin-specific routes (e.g. git smart HTTP proxy)
    for plugin in plugins.values():
        for method, path, handler in plugin.get_routes():
            app.router.add_route(method, path, handler)

    return app


def create_app(config: dict) -> web.Application:
    """Create the full application with plugin discovery."""
    from fgap.plugins import discover_plugins

    plugins = discover_plugins(config)
    return create_routes(config, plugins)
