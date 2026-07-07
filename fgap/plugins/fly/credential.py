from fgap.plugins.base import match_resource


def select_credential(resource: str, config: dict) -> dict | None:
    """Select credential for a Fly.io app.

    The resource is the Fly app name (e.g. "my-app"). Patterns follow
    the usual fgap rules, so tokens can be scoped per app ("my-app"),
    per prefix family via fnmatch ("my-app-*"), or org-wide ("*").
    First-match-wins over the credentials array.

    Returns:
        {"env": {"FLY_API_TOKEN": ..., "FLY_NO_UPDATE_CHECK": "1"}} or None.
    """
    for cred in config.get("credentials", []):
        for pattern in cred.get("resources", []):
            if match_resource(pattern, resource):
                return {"env": {
                    "FLY_API_TOKEN": cred["token"],
                    # a version-check prompt would corrupt machine-read output
                    "FLY_NO_UPDATE_CHECK": "1",
                }}
    return None
