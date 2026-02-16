import fnmatch


def match_resource(pattern: str, resource: str) -> bool:
    """Check if resource pattern matches (case-insensitive).

    Patterns:
    - "*" matches all resources
    - "owner/*" matches all repos of that owner
    - "owner/repo" matches exactly
    - fnmatch patterns (e.g. "owner/repo-?") for advanced matching
    """
    p = pattern.lower()
    r = resource.lower()
    if p == "*":
        return True
    if p.endswith("/*"):
        return r.split("/")[0] == p[:-2]
    return fnmatch.fnmatch(r, p)


def select_credential(resource: str, config: dict) -> dict | None:
    """Select credential for a GitHub resource.

    First-match-wins over the credentials array.

    Returns:
        {"env": {"GH_TOKEN": "...", "GH_HOST": "github.com"}} or None.
    """
    for cred in config.get("credentials", []):
        for pattern in cred.get("resources", []):
            if match_resource(pattern, resource):
                return {
                    "env": {
                        "GH_TOKEN": cred["token"],
                        "GH_HOST": "github.com",
                    }
                }
    return None
