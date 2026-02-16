"""Credential masking for log output.

Collects secret values from config and replaces them with '***' in log messages.
"""

import logging

SECRET_KEYS = frozenset({
    "token",
    "keyring_password",
    "client_secret",
    "refresh_token",
    "password",
})


def collect_secrets(config: dict) -> set[str]:
    """Recursively collect secret values from config.

    Walks the config tree and collects string values whose keys are
    in SECRET_KEYS.
    """
    secrets = set()
    _walk(config, secrets)
    return secrets


def _walk(obj, secrets: set[str]) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in SECRET_KEYS and isinstance(value, str) and value:
                secrets.add(value)
            else:
                _walk(value, secrets)
    elif isinstance(obj, list):
        for item in obj:
            _walk(item, secrets)


def mask_value(value: str, visible_prefix: int = 8) -> str:
    """Mask a credential value, keeping the first few characters visible.

    Example: mask_value("ghp_abc123xyz") -> "ghp_abc1***"
    """
    if len(value) <= visible_prefix:
        return "***"
    return value[:visible_prefix] + "***"


def mask_secrets(text: str, secrets: set[str]) -> str:
    """Replace all secret values in text with '***'."""
    for secret in secrets:
        text = text.replace(secret, "***")
    return text


class MaskingFormatter(logging.Formatter):
    """Formatter that masks secrets in log output."""

    def __init__(self, fmt: str, secrets: set[str], **kwargs):
        super().__init__(fmt, **kwargs)
        self.secrets = secrets

    def format(self, record: logging.LogRecord) -> str:
        result = super().format(record)
        if self.secrets:
            return mask_secrets(result, self.secrets)
        return result
