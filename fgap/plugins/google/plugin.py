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
        self, config: dict, *, _run_gog=None,
    ) -> list[dict]:
        """Check gog credential validity.

        For each credential, runs ``gog auth list`` to verify the
        keyring is accessible and accounts are configured.
        """
        _run_gog = _run_gog or _default_run_gog
        results = []
        for cred in config.get("credentials", []):
            keyring_pw = cred.get("keyring_password", "")
            entry = {
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
