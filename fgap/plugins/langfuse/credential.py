from fgap.plugins.base import match_resource


def select_credential(resource: str, config: dict) -> dict | None:
    """Select credential for a Langfuse resource.

    First-match-wins over the credentials array.

    Returns:
        {"env": {"LANGFUSE_PUBLIC_KEY": "...", "LANGFUSE_SECRET_KEY": "...", ...}}
        or None.
    """
    for cred in config.get("credentials", []):
        for pattern in cred.get("resources", []):
            if match_resource(pattern, resource):
                env = {
                    "LANGFUSE_PUBLIC_KEY": cred["public_key"],
                    "LANGFUSE_SECRET_KEY": cred["secret_key"],
                }
                if "host" in cred:
                    env["LANGFUSE_BASE_URL"] = cred["host"]
                return {"env": env}
    return None
