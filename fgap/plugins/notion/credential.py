from fgap.plugins.base import match_resource


def select_credential(resource: str, config: dict) -> dict | None:
    """Select credential for a Notion resource.

    First-match-wins over the credentials array.

    Returns:
        {"env": {"NOTION_TOKEN": "..."}} or None.
    """
    for cred in config.get("credentials", []):
        for pattern in cred.get("resources", []):
            if match_resource(pattern, resource):
                return {"env": {"NOTION_TOKEN": cred["token"]}}
    return None
