from fgap.plugins.base import Plugin


def select_credential(
    tool: str,
    resource: str,
    config: dict,
    plugins: dict[str, Plugin],
) -> dict | None:
    """Select credential for the given tool and resource.

    Routes to the correct plugin based on the tool field.

    Returns:
        Credential dict with 'env' key, or None if no match.
    """
    for plugin in plugins.values():
        if tool in plugin.tools:
            plugin_config = config.get("plugins", {}).get(plugin.name, {})
            return plugin.select_credential(resource, plugin_config)
    return None
