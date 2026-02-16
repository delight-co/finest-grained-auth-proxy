from fgap.plugins.base import match_resource


def select_credential(resource: str, config: dict) -> dict | None:
    """Select credential for a Google resource.

    First-match-wins over the credentials array.

    Returns:
        {"env": {"GOG_KEYRING_PASSWORD": "..."}} or None.
        Includes GOG_ACCOUNT if the credential specifies an account.
    """
    for cred in config.get("credentials", []):
        if "keyring_password" not in cred:
            continue
        for pattern in cred.get("resources", []):
            if match_resource(pattern, resource):
                env = {"GOG_KEYRING_PASSWORD": cred["keyring_password"]}
                if "account" in cred:
                    env["GOG_ACCOUNT"] = cred["account"]
                return {"env": env}
    return None
