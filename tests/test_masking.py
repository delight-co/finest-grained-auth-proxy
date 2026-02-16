import logging

from fgap.core.masking import MaskingFormatter, collect_secrets, mask_secrets


class TestCollectSecrets:
    def test_collects_token(self):
        config = {"plugins": {"github": {"credentials": [
            {"token": "ghp_secret123", "resources": ["*"]},
        ]}}}
        assert "ghp_secret123" in collect_secrets(config)

    def test_collects_keyring_password(self):
        config = {"plugins": {"google": {"credentials": [
            {"keyring_password": "my-pw", "resources": ["*"]},
        ]}}}
        assert "my-pw" in collect_secrets(config)

    def test_collects_multiple_secrets(self):
        config = {"plugins": {
            "github": {"credentials": [
                {"token": "tok1", "resources": ["a/*"]},
                {"token": "tok2", "resources": ["b/*"]},
            ]},
            "google": {"credentials": [
                {"keyring_password": "pw1", "resources": ["*"]},
            ]},
        }}
        secrets = collect_secrets(config)
        assert secrets == {"tok1", "tok2", "pw1"}

    def test_ignores_non_secret_keys(self):
        config = {"plugins": {"github": {"credentials": [
            {"token": "secret", "resources": ["owner/repo"]},
        ]}}}
        secrets = collect_secrets(config)
        assert "secret" in secrets
        assert "owner/repo" not in secrets

    def test_ignores_empty_string(self):
        config = {"plugins": {"github": {"credentials": [
            {"token": "", "resources": ["*"]},
        ]}}}
        assert collect_secrets(config) == set()

    def test_empty_config(self):
        assert collect_secrets({}) == set()

    def test_nested_secret_keys(self):
        config = {"a": {"b": {"password": "deep_secret"}}}
        assert "deep_secret" in collect_secrets(config)

    def test_client_secret_and_refresh_token(self):
        config = {"plugins": {"google": {"credentials": [
            {
                "client_secret": "cs_xxx",
                "refresh_token": "rt_yyy",
                "resources": ["*"],
            },
        ]}}}
        secrets = collect_secrets(config)
        assert "cs_xxx" in secrets
        assert "rt_yyy" in secrets


class TestMaskSecrets:
    def test_replaces_secret(self):
        assert mask_secrets("token is ghp_abc123", {"ghp_abc123"}) == "token is ***"

    def test_replaces_multiple(self):
        result = mask_secrets("a=tok1 b=tok2", {"tok1", "tok2"})
        assert "tok1" not in result
        assert "tok2" not in result
        assert "***" in result

    def test_no_secrets(self):
        assert mask_secrets("safe text", set()) == "safe text"

    def test_secret_not_present(self):
        assert mask_secrets("safe text", {"ghp_xxx"}) == "safe text"

    def test_partial_match(self):
        assert mask_secrets("ghp_abc123_extra", {"ghp_abc123"}) == "***_extra"


class TestMaskingFormatter:
    def test_masks_in_log_output(self):
        secrets = {"ghp_secret"}
        formatter = MaskingFormatter("%(message)s", secrets)
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="token is ghp_secret", args=(), exc_info=None,
        )
        assert formatter.format(record) == "token is ***"

    def test_masks_in_format_args(self):
        secrets = {"ghp_secret"}
        formatter = MaskingFormatter("%(message)s", secrets)
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="token is %s", args=("ghp_secret",), exc_info=None,
        )
        assert formatter.format(record) == "token is ***"

    def test_no_secrets_passthrough(self):
        formatter = MaskingFormatter("%(message)s", set())
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="safe message", args=(), exc_info=None,
        )
        assert formatter.format(record) == "safe message"

    def test_masks_in_full_format(self):
        secrets = {"ghp_secret"}
        formatter = MaskingFormatter("%(name)s: %(message)s", secrets)
        record = logging.LogRecord(
            name="fgap", level=logging.INFO, pathname="", lineno=0,
            msg="loaded ghp_secret", args=(), exc_info=None,
        )
        assert "ghp_secret" not in formatter.format(record)
        assert "***" in formatter.format(record)
