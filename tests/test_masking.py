import logging

from fgap.core.masking import (
    MaskingFormatter,
    collect_secrets,
    mask_email,
    mask_emails_in_text,
    mask_secrets,
    mask_value,
)


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


class TestMaskValue:
    def test_long_value(self):
        assert mask_value("ghp_abc123xyz") == "ghp_abc1***"

    def test_exactly_prefix_length(self):
        assert mask_value("12345678") == "***"

    def test_shorter_than_prefix(self):
        assert mask_value("short") == "***"

    def test_custom_prefix(self):
        assert mask_value("test-password-123", visible_prefix=4) == "test***"

    def test_empty_string(self):
        assert mask_value("") == "***"


class TestMaskEmail:
    def test_normal_email(self):
        assert mask_email("user@example.com") == "us***@example.com"

    def test_short_local_part(self):
        assert mask_email("ab@example.com") == "***@example.com"

    def test_single_char_local(self):
        assert mask_email("a@example.com") == "***@example.com"

    def test_long_local_part(self):
        assert mask_email("longusername@gmail.com") == "lo***@gmail.com"

    def test_no_at_sign(self):
        assert mask_email("not-an-email") == "not-an-email"

    def test_dots_in_local(self):
        assert mask_email("first.last@example.com") == "fi***@example.com"

    def test_subdomain(self):
        assert mask_email("user@mail.example.co.jp") == "us***@mail.example.co.jp"


class TestMaskEmailsInText:
    def test_single_email(self):
        assert mask_emails_in_text("account: user@example.com") == "account: us***@example.com"

    def test_multiple_emails(self):
        text = "user1@gmail.com\nuser2@example.com"
        result = mask_emails_in_text(text)
        assert "us***@gmail.com" in result
        assert "us***@example.com" in result
        assert "user1" not in result
        assert "user2" not in result

    def test_no_emails(self):
        assert mask_emails_in_text("no emails here") == "no emails here"

    def test_empty_string(self):
        assert mask_emails_in_text("") == ""


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
