from fgap.plugins.base import Plugin

_registry: dict[str, type[Plugin]] = {}


def register_plugin(plugin_cls: type[Plugin]) -> type[Plugin]:
    """Register a plugin class. Can be used as a decorator."""
    instance = plugin_cls()
    name = instance.name
    if name in _registry:
        raise ValueError(f"Plugin '{name}' already registered")
    _registry[name] = plugin_cls
    return plugin_cls


def discover_plugins(config: dict) -> dict[str, Plugin]:
    """Instantiate registered plugins that have config entries."""
    plugins = {}
    plugin_configs = config.get("plugins", {})
    for name, plugin_cls in _registry.items():
        if name in plugin_configs:
            plugins[name] = plugin_cls()
    return plugins


def clear_registry() -> None:
    """Clear all registered plugins. For testing."""
    _registry.clear()
