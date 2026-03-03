import asyncio
import os

from fgap.core.masking import mask_emails_in_text, mask_value
from fgap.plugins.base import Plugin


class GooglePlugin(Plugin):
    """Google plugin: gog CLI execution for Google Workspace."""

    @property
    def name(self) -> str:
        return "google"

    @property
    def tools(self) -> list[str]:
        return ["gog"]

    def select_credential(self, resource: str, config: dict) -> dict | None:
        from .credential import select_credential

        return select_credential(resource, config)

    async def health_check(
        self, config: dict, *, _run_gog=None, _check_sa=None,
    ) -> list[dict]:
        """Check gog credential validity.

        For OAuth credentials, runs ``gog auth list`` to verify the
        keyring is accessible and accounts are configured.
        For SA credentials, checks that the key file exists and is readable.
        """
        _run_gog = _run_gog or _default_run_gog
        _check_sa = _check_sa or _default_check_sa
        results = []
        for cred in config.get("credentials", []):
            if "sa_key_file" in cred:
                entry = {
                    "type": "service_account",
                    "account": mask_emails_in_text(cred.get("account", "")),
                    "resources": cred.get("resources", []),
                }
                try:
                    status = _check_sa(cred["sa_key_file"])
                    entry.update(status)
                except Exception as e:
                    entry.update({"valid": False, "error": str(e)})
                results.append(entry)
            else:
                keyring_pw = cred.get("keyring_password", "")
                entry = {
                    "type": "oauth",
                    "masked_keyring_password": mask_value(keyring_pw, visible_prefix=4),
                    "resources": cred.get("resources", []),
                }
                try:
                    status = await _run_gog(keyring_pw)
                    if "accounts" in status:
                        status["accounts"] = mask_emails_in_text(
                            status["accounts"],
                        )
                    entry.update(status)
                except Exception as e:
                    entry.update({"valid": False, "error": str(e)})
                results.append(entry)
        return results


def _default_check_sa(sa_key_file: str) -> dict:
    if not os.path.isfile(sa_key_file):
        return {"valid": False, "error": f"SA key file not found: {sa_key_file}"}
    if not os.access(sa_key_file, os.R_OK):
        return {"valid": False, "error": f"SA key file not readable: {sa_key_file}"}
    return {"valid": True}


async def _default_run_gog(keyring_password: str) -> dict:
    env = {**os.environ, "GOG_KEYRING_PASSWORD": keyring_password}
    try:
        proc = await asyncio.create_subprocess_exec(
            "gog", "auth", "list",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
    except FileNotFoundError:
        return {"valid": False, "error": "gog binary not found"}
    except asyncio.TimeoutError:
        return {"valid": False, "error": "gog auth list timed out"}

    if proc.returncode == 0:
        return {
            "valid": True,
            "accounts": stdout.decode("utf-8", errors="replace").strip(),
        }
    return {
        "valid": False,
        "error": stderr.decode("utf-8", errors="replace").strip()
        or f"exit code {proc.returncode}",
    }
