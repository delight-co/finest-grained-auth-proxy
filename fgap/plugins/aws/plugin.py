import asyncio
import json

from fgap.core.masking import mask_value
from fgap.plugins.base import Plugin

_HEALTH_TIMEOUT_SEC = 10


class AwsPlugin(Plugin):
    """AWS plugin: read-only aws CLI execution with credential injection.

    The resource is a config-defined account alias. The permission
    grammar (curated per-service read-only operation table) lives in
    policy.py; the config grants services per credential entry. Scopes
    are per service, not per workload — if multiple workloads share the
    account, reads span all of them.
    """

    @property
    def name(self) -> str:
        return "aws"

    @property
    def tools(self) -> list[str]:
        return ["aws"]

    def select_credential(self, resource: str, config: dict) -> dict | None:
        from .credential import select_credential

        return select_credential(resource, config)

    def check_policy(self, args: list[str], resource: str,
                     config: dict) -> str | None:
        from .policy import check_policy

        return check_policy(args, resource, config)

    def validate_config(self, config: dict) -> None:
        """Strict schema: every entry names its account, services, and
        exactly one credential source (profile xor key pair)."""
        from fgap.core.config import ConfigError, check_keys

        from .policy import KNOWN_SERVICES

        check_keys(config, required={"credentials"}, context="plugins.aws")
        for i, cred in enumerate(config["credentials"]):
            ctx = f"plugins.aws credential {i}"
            check_keys(
                cred,
                required={"resources", "services"},
                optional={"profile", "access_key_id", "secret_access_key",
                          "region"},
                context=ctx,
            )
            has_profile = "profile" in cred
            has_keys = "access_key_id" in cred or "secret_access_key" in cred
            if has_profile and has_keys:
                raise ConfigError(
                    f"{ctx}: use either 'profile' or the "
                    f"'access_key_id'/'secret_access_key' pair, not both"
                )
            if not has_profile:
                if not ("access_key_id" in cred
                        and "secret_access_key" in cred):
                    raise ConfigError(
                        f"{ctx}: needs 'profile' or both 'access_key_id' "
                        f"and 'secret_access_key'"
                    )
            resources = cred["resources"]
            if not isinstance(resources, list) or not resources:
                raise ConfigError(
                    f"{ctx}: 'resources' must be a non-empty array"
                )
            services = cred["services"]
            if not isinstance(services, list) or not services:
                raise ConfigError(
                    f"{ctx}: 'services' must be a non-empty array"
                )
            unknown = set(services) - KNOWN_SERVICES
            if unknown:
                raise ConfigError(
                    f"{ctx}: unknown service(s): "
                    f"{', '.join(sorted(unknown))} "
                    f"(supported: {', '.join(sorted(KNOWN_SERVICES))})"
                )

    async def health_check(self, config: dict) -> list[dict]:
        """Verify each entry with ``sts get-caller-identity``.

        GetCallerIdentity needs no IAM permission, so a success proves
        the credential resolves without depending on the read grants.
        """
        results = []
        for cred in config.get("credentials", []):
            from .credential import select_credential

            alias = (cred.get("resources") or ["?"])[0]
            entry: dict = {
                "credential": (
                    f"profile:{cred['profile']}" if "profile" in cred
                    else mask_value(cred.get("access_key_id", ""))
                ),
                "resources": cred.get("resources", []),
                "services": cred.get("services", []),
            }
            selected = select_credential(alias, {"credentials": [cred]})
            env = selected["env"] if selected else {}
            try:
                entry.update(await _caller_identity(env))
            except Exception as e:
                entry.update({"valid": False, "error": str(e)})
            results.append(entry)
        return results


async def _caller_identity(env: dict) -> dict:
    import os

    proc = await asyncio.create_subprocess_exec(
        "aws", "sts", "get-caller-identity", "--output", "json",
        env={**os.environ, **env},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_HEALTH_TIMEOUT_SEC,
        )
    except TimeoutError:
        proc.kill()
        return {"valid": False, "error": "sts get-caller-identity timed out"}
    if proc.returncode != 0:
        return {
            "valid": False,
            "error": stderr.decode(errors="replace").strip()[:200],
        }
    identity = json.loads(stdout.decode(errors="replace"))
    return {
        "valid": True,
        "account": identity.get("Account", ""),
        "arn": identity.get("Arn", ""),
    }
