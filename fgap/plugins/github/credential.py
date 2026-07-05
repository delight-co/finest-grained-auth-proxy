from fgap.plugins.base import match_resource


def select_credential(resource: str, config: dict) -> dict | None:
    """Select credential for a GitHub resource.

    First-match-wins over the credentials array. Two credential shapes:

    - PAT: {"token": "...", "resources": [...]} -> resolved here
    - GitHub App: {"app_id": ..., "installation_id": ...,
      "private_key_path"|"private_key": ..., "resources": [...]} ->
      selected here, minted lazily in resolve_credential_env (async)

    Returns:
        {"env": {...}} for PATs, {"app": cred, "resource": resource} for
        App credentials, or None if nothing matches.
    """
    for cred in config.get("credentials", []):
        for pattern in cred.get("resources", []):
            if match_resource(pattern, resource):
                if "app_id" in cred:
                    return {"app": cred, "resource": resource}
                return {
                    "env": {
                        "GH_TOKEN": cred["token"],
                        "GH_HOST": "github.com",
                    }
                }
    return None
