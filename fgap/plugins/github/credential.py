from fgap.plugins.base import match_resource


def select_credential(resource: str, config: dict) -> dict | None:
    """Select credential for a GitHub resource.

    First-match-wins over the credentials array.

    Returns:
        {"env": {"GH_TOKEN": "...", "GH_HOST": "github.com"}} or None.
    """
    for cred in config.get("credentials", []):
        for pattern in cred.get("resources", []):
            if match_resource(pattern, resource):
                return {
                    "env": {
                        "GH_TOKEN": cred["token"],
                        "GH_HOST": "github.com",
                    }
                }
    return None
