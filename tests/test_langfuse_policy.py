import pytest

from fgap.core.config import ConfigError
from fgap.plugins.langfuse.plugin import LangfusePlugin
from fgap.plugins.langfuse.policy import check_policy


def _config(permissions, resources=None):
    return {"credentials": [
        {
            "public_key": "pk-lf-test",
            "secret_key": "sk-lf-test",
            "resources": resources or ["my-project"],
            "permissions": permissions,
        },
    ]}


class TestCheckPolicy:
    def test_list_allowed_with_read(self):
        assert check_policy(
            ["api", "traces", "list"], "my-project", _config(["read"]),
        ) is None

    def test_get_allowed_with_read(self):
        assert check_policy(
            ["api", "traces", "get", "abc"], "my-project", _config(["read"]),
        ) is None

    def test_schema_is_read(self):
        assert check_policy(
            ["api", "__schema"], "my-project", _config(["read"]),
        ) is None

    def test_write_denied_without_write(self):
        reason = check_policy(
            ["api", "prompts", "create"], "my-project", _config(["read"]),
        )
        assert reason is not None
        assert "'write' permission is not granted" in reason

    def test_write_allowed_with_write(self):
        assert check_policy(
            ["api", "prompts", "create"], "my-project",
            _config(["read", "write"]),
        ) is None

    def test_read_denied_with_write_only(self):
        reason = check_policy(
            ["api", "traces", "list"], "my-project", _config(["write"]),
        )
        assert reason is not None
        assert "'read' permission is not granted" in reason

    def test_unknown_verb_denied_even_with_all_grants(self):
        reason = check_policy(
            ["api", "traces", "truncate"], "my-project",
            _config(["read", "write"]),
        )
        assert reason is not None
        assert "unrecognized" in reason

    def test_bare_api_denied(self):
        reason = check_policy(["api"], "my-project", _config(["read"]))
        assert reason is not None
        assert "unrecognized" in reason

    def test_missing_verb_denied(self):
        reason = check_policy(
            ["api", "traces"], "my-project", _config(["read"]),
        )
        assert reason is not None
        assert "unrecognized" in reason

    def test_help_allowed_without_matching_grant(self):
        assert check_policy(
            ["api", "traces", "--help"], "my-project", _config(["read"]),
        ) is None
        assert check_policy(
            ["api", "prompts", "create", "-h"], "my-project",
            _config(["read"]),
        ) is None

    def test_grants_ride_first_match_wins_routing(self):
        config = {"credentials": [
            {"public_key": "pk-a", "secret_key": "sk-a",
             "resources": ["proj-a"], "permissions": ["read"]},
            {"public_key": "pk-b", "secret_key": "sk-b",
             "resources": ["*"], "permissions": ["read", "write"]},
        ]}
        assert check_policy(
            ["api", "prompts", "create"], "proj-a", config,
        ) is not None
        assert check_policy(
            ["api", "prompts", "create"], "proj-b", config,
        ) is None

    def test_unmatched_resource_falls_through(self):
        # Policy allows so that credential selection can fail with its
        # clearer "No credential for ..." message.
        assert check_policy(
            ["api", "traces", "list"], "no-such-project", _config(["read"]),
        ) is None


class TestValidateConfig:
    def _entry(self, **overrides):
        entry = {
            "public_key": "pk-lf-test",
            "secret_key": "sk-lf-test",
            "resources": ["my-project"],
            "permissions": ["read"],
        }
        entry.update(overrides)
        return entry

    def test_valid_passes(self):
        LangfusePlugin().validate_config({"credentials": [self._entry()]})

    def test_host_is_optional(self):
        LangfusePlugin().validate_config({"credentials": [
            self._entry(host="https://us.cloud.langfuse.com"),
        ]})

    def test_missing_permissions_rejected(self):
        entry = self._entry()
        del entry["permissions"]
        with pytest.raises(ConfigError, match="permissions"):
            LangfusePlugin().validate_config({"credentials": [entry]})

    def test_unknown_key_rejected(self):
        with pytest.raises(ConfigError, match="unknown key"):
            LangfusePlugin().validate_config({"credentials": [
                self._entry(projcet="typo"),
            ]})

    def test_unknown_permission_rejected(self):
        with pytest.raises(ConfigError, match="unknown permission"):
            LangfusePlugin().validate_config({"credentials": [
                self._entry(permissions=["read", "admin"]),
            ]})

    def test_empty_permissions_rejected(self):
        with pytest.raises(ConfigError, match="permissions"):
            LangfusePlugin().validate_config({"credentials": [
                self._entry(permissions=[]),
            ]})

    def test_empty_resources_rejected(self):
        with pytest.raises(ConfigError, match="resources"):
            LangfusePlugin().validate_config({"credentials": [
                self._entry(resources=[]),
            ]})

    def test_missing_credentials_key_rejected(self):
        with pytest.raises(ConfigError, match="credentials"):
            LangfusePlugin().validate_config({})

    def test_unknown_plugin_level_key_rejected(self):
        with pytest.raises(ConfigError, match="unknown key"):
            LangfusePlugin().validate_config({
                "credentials": [self._entry()],
                "permissions": ["read"],
            })
