from fgap.plugins.base import match_resource


def select_credential(resource: str, config: dict) -> dict | None:
    """Select credential for a Google resource.

    First-match-wins over the credentials array.
    Supports two credential types:
    - OAuth (keyring_password): injects GOG_KEYRING_PASSWORD
    - Service account (sa_key_file + account): injects GOG_ACCOUNT

    SA key must be pre-provisioned to gog's expected path
    (~/.config/gogcli/sa-{base64url(email)}.json) by the infrastructure
    layer (e.g. user_data.sh). fgap only injects the GOG_ACCOUNT env var.

    Returns:
        {"env": {...}} or None.
    """
    for cred in config.get("credentials", []):
        is_sa = "sa_key_file" in cred
        is_oauth = "keyring_password" in cred
        if not is_sa and not is_oauth:
            continue
        for pattern in cred.get("resources", []):
            if match_resource(pattern, resource):
                if is_sa:
                    return {"env": {"GOG_ACCOUNT": cred["account"]}}
                env = {"GOG_KEYRING_PASSWORD": cred["keyring_password"]}
                if "account" in cred:
                    env["GOG_ACCOUNT"] = cred["account"]
                return {"env": env}
    return None
