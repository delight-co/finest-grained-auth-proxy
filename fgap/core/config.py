import os
import stat

import json5


class ConfigError(Exception):
    """Raised when config file is invalid."""


def load_config(path: str) -> dict:
    """Load and validate config from a JSON5 file.

    Validates:
    - File exists
    - File permissions are 600 (owner read/write only)
    - JSON5 is valid
    - Required structure is present
    """
    if not os.path.isfile(path):
        raise ConfigError(f"Config file not found: {path}")

    file_stat = os.stat(path)
    mode = stat.S_IMODE(file_stat.st_mode)
    group_or_other = (
        stat.S_IRGRP | stat.S_IWGRP | stat.S_IXGRP
        | stat.S_IROTH | stat.S_IWOTH | stat.S_IXOTH
    )
    if mode & group_or_other:
        raise ConfigError(
            f"Config file {path} has too-open permissions ({oct(mode)}). "
            f"Run: chmod 600 {path}"
        )

    with open(path) as f:
        try:
            config = json5.load(f)
        except ValueError as e:
            raise ConfigError(f"Invalid JSON5 in {path}: {e}") from e

    if not isinstance(config, dict):
        raise ConfigError("Config must be a JSON object")

    plugins = config.get("plugins", {})
    if not isinstance(plugins, dict):
        raise ConfigError("'plugins' must be an object")

    for plugin_name, plugin_config in plugins.items():
        _validate_plugin_config(plugin_name, plugin_config)

    return config


def check_keys(obj: dict, *, required: set[str], context: str,
               optional: set[str] = frozenset()) -> None:
    """Strict key check for plugin config schemas.

    Everything not explicitly optional is required, and keys outside the
    schema are rejected — a config that is missing something or contains
    something unrecognized is wrong either way.

    Raises:
        ConfigError: listing the missing / unknown keys.
    """
    missing = required - obj.keys()
    if missing:
        raise ConfigError(
            f"{context}: missing required key(s): {', '.join(sorted(missing))}"
        )
    unknown = obj.keys() - required - optional
    if unknown:
        raise ConfigError(
            f"{context}: unknown key(s): {', '.join(sorted(unknown))}"
        )


def _validate_plugin_config(name: str, plugin_config: dict) -> None:
    if not isinstance(plugin_config, dict):
        raise ConfigError(f"Plugin config '{name}' must be an object")

    credentials = plugin_config.get("credentials", [])
    if not isinstance(credentials, list):
        raise ConfigError(f"Plugin '{name}' credentials must be an array")

    for i, cred in enumerate(credentials):
        if not isinstance(cred, dict):
            raise ConfigError(f"Plugin '{name}' credential {i} must be an object")
        if "resources" not in cred:
            raise ConfigError(f"Plugin '{name}' credential {i} missing 'resources'")
        if not isinstance(cred["resources"], list):
            raise ConfigError(f"Plugin '{name}' credential {i} 'resources' must be an array")
