import base64
import logging
import os
import shutil

from fgap.plugins.base import match_resource

logger = logging.getLogger(__name__)

_provisioned_sa_keys: set[str] = set()


def _provision_sa_key(sa_key_file: str, account: str) -> None:
    """Copy SA key to the path gog expects: ~/.config/gogcli/sa-{base64url(email)}.json"""
    if account in _provisioned_sa_keys:
        return
    safe_email = base64.urlsafe_b64encode(
        account.lower().strip().encode(),
    ).decode().rstrip("=")
    gog_config_dir = os.path.expanduser("~/.config/gogcli")
    os.makedirs(gog_config_dir, exist_ok=True)
    dest = os.path.join(gog_config_dir, f"sa-{safe_email}.json")
    shutil.copy2(sa_key_file, dest)
    os.chmod(dest, 0o600)
    logger.info("provisioned SA key for %s -> %s", account, dest)
    _provisioned_sa_keys.add(account)


def select_credential(resource: str, config: dict) -> dict | None:
    """Select credential for a Google resource.

    First-match-wins over the credentials array.
    Supports two credential types:
    - OAuth (keyring_password): injects GOG_KEYRING_PASSWORD
    - Service account (sa_key_file + account): provisions SA key and injects GOG_ACCOUNT

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
                    _provision_sa_key(cred["sa_key_file"], cred["account"])
                    return {"env": {"GOG_ACCOUNT": cred["account"]}}
                env = {"GOG_KEYRING_PASSWORD": cred["keyring_password"]}
                if "account" in cred:
                    env["GOG_ACCOUNT"] = cred["account"]
                return {"env": env}
    return None
