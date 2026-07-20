"""Policy judgment for the aws CLI (read-only observability).

Verb prefixes alone (describe-*/get-*/list-*) are not a safe grammar for
AWS. Three trap classes force a curated per-service (service, operation)
table:

1. Reads that return secrets: ``ssm get-parameter``, ``secretsmanager
   get-secret-value``, ``lambda get-function`` (env vars). Those
   services are not supported at all.
2. Read-intent operations with write-shaped verbs: ``logs start-query``
   / ``get-query-results`` (CloudWatch Logs Insights) — explicitly
   included, excluding them would gut log search.
3. Read-shaped operations with side effects or credential minting:
   ``sqs receive-message`` (consumes visibility), ``s3 presign``,
   ``ecr get-login-password`` (docker login) — not listed, so denied.

Anything not in the table is denied (allowlist). Global flags before the
(service, operation) tokens are themselves allowlisted; ``--profile`` /
``--endpoint-url`` / ``--debug`` are denied anywhere (credential routing
belongs to fgap, endpoint redirection and signing-debug output leak the
proxy-side credential context).

Pair this grammar with a read-only IAM principal (see
aws-readonly-policy.example.json): the two layers fail independently.
"""

from fgap.plugins.base import match_resource

# Curated read operations per service. Every allowed pair is explicit.
READ_OPERATIONS: dict[str, frozenset[str]] = {
    "logs": frozenset({
        "describe-log-groups",
        "describe-log-streams",
        "get-log-events",
        "filter-log-events",
        "get-log-record",
        "start-query",
        "get-query-results",
        "stop-query",
        "tail",  # bounded reads only; --follow is denied below
    }),
    "ecs": frozenset({
        "describe-clusters",
        "describe-services",
        "describe-tasks",
        "describe-task-definition",
        "list-clusters",
        "list-services",
        "list-tasks",
        "list-task-definitions",
    }),
    "cloudwatch": frozenset({
        "get-metric-data",
        "get-metric-statistics",
        "list-metrics",
        "describe-alarms",
    }),
    "ecr": frozenset({
        "describe-repositories",
        "describe-images",
        "list-images",
    }),
}

KNOWN_SERVICES = frozenset(READ_OPERATIONS)

# Global flags tolerated before the (service, operation) tokens.
# Unrecognized flags in the global position are denied.
_GLOBAL_FLAGS_WITH_VALUE = frozenset({
    "--region", "--output", "--query", "--color",
    "--max-items", "--page-size", "--starting-token",
})
_GLOBAL_FLAGS_BOOL = frozenset({"--no-cli-pager", "--no-paginate"})

# Flags denied anywhere in argv, with the reason.
_DENIED_ANYWHERE = {
    "--profile": "credential routing is fgap's job (select with the account alias)",
    "--endpoint-url": "endpoint redirection is not allowed",
    "--debug": "debug output leaks signing context",
    "--follow": "long-running streams are not supported; use bounded reads (e.g. --since)",
    "--with-decryption": "decrypting reads are not allowed",
}


def _parse(args: list[str]) -> tuple[str | None, str | None, str | None]:
    """Extract (service, operation) from argv.

    Returns (service, operation, deny_reason). Only allowlisted global
    flags may appear before the two command tokens; parameters after the
    operation are the operation's own and are not enumerated here
    (the denied-anywhere list still applies to them).
    """
    service: str | None = None
    operation: str | None = None
    i = 0
    while i < len(args) and operation is None:
        arg = args[i]
        if arg.startswith("-"):
            flag = arg.split("=", 1)[0]
            if flag in _GLOBAL_FLAGS_BOOL:
                i += 1
                continue
            if flag in _GLOBAL_FLAGS_WITH_VALUE:
                i += 1 if "=" in arg else 2
                continue
            return None, None, (
                f"global flag '{flag}' is not allowed before the service "
                f"(allowed: {', '.join(sorted(_GLOBAL_FLAGS_WITH_VALUE | _GLOBAL_FLAGS_BOOL))})"
            )
        if service is None:
            service = arg
        else:
            operation = arg
        i += 1
    return service, operation, None


def check_policy(args: list[str], resource: str, config: dict) -> str | None:
    """Return None to allow, or a deny reason."""
    if any(a in ("-h", "--help") for a in args):
        return None  # help output only, no API access

    for arg in args:
        flag = arg.split("=", 1)[0]
        if flag in _DENIED_ANYWHERE:
            return f"'{flag}' is not allowed: {_DENIED_ANYWHERE[flag]}"

    service, operation, err = _parse(args)
    if err is not None:
        return err
    if service == "help" or operation == "help":
        return None
    if service is None or operation is None:
        return "expected 'aws [global flags] <service> <operation> ...'"

    operations = READ_OPERATIONS.get(service)
    if operations is None:
        return (
            f"service '{service}' is not supported "
            f"(supported: {', '.join(sorted(KNOWN_SERVICES))})"
        )
    if operation not in operations:
        return (
            f"'{service} {operation}' is not in the read-only allowlist "
            f"(allowed for {service}: {', '.join(sorted(operations))})"
        )

    # Grant check: services ride the credential entry that will serve
    # this resource (first-match-wins, same routing as the credential).
    for cred in config.get("credentials", []):
        for pattern in cred.get("resources", []):
            if match_resource(pattern, resource):
                granted = cred.get("services", [])
                if service in granted:
                    return None
                return (
                    f"service '{service}' is not granted for account "
                    f"'{resource}' (granted: {', '.join(granted) or 'none'})"
                )

    # No credential entry matches: allow here so credential selection
    # can fail with its clearer "No credential for ..." message.
    return None
