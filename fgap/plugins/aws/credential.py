from fgap.plugins.base import match_resource


def select_credential(resource: str, config: dict) -> dict | None:
    """Select credential for an AWS account alias.

    The resource is a config-defined account alias (matched against
    ``resources`` patterns, first-match-wins). The entry either names a
    proxy-host AWS profile (resolved by the host's ``~/.aws``, SSO
    included) or carries an explicit key pair.

    Returns:
        {"env": {...}} or None.
    """
    for cred in config.get("credentials", []):
        for pattern in cred.get("resources", []):
            if match_resource(pattern, resource):
                env: dict[str, str] = {}
                if "profile" in cred:
                    env["AWS_PROFILE"] = cred["profile"]
                else:
                    env["AWS_ACCESS_KEY_ID"] = cred["access_key_id"]
                    env["AWS_SECRET_ACCESS_KEY"] = cred["secret_access_key"]
                if "region" in cred:
                    env["AWS_DEFAULT_REGION"] = cred["region"]
                # machine-read output must never block on a pager
                env["AWS_PAGER"] = ""
                return {"env": env}
    return None
