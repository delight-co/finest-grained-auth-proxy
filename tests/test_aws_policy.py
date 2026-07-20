import pytest

from fgap.core.config import ConfigError
from fgap.plugins.aws.plugin import AwsPlugin
from fgap.plugins.aws.policy import check_policy


def _config(services, resources=None):
    return {"credentials": [
        {
            "profile": "readonly",
            "resources": resources or ["my-account"],
            "services": services,
        },
    ]}


FULL = ["logs", "ecs", "cloudwatch", "ecr"]


class TestCheckPolicy:
    def test_logs_tail_allowed(self):
        assert check_policy(
            ["logs", "tail", "/my/group", "--since", "10m"],
            "my-account", _config(FULL),
        ) is None

    def test_ecs_describe_services_allowed(self):
        assert check_policy(
            ["ecs", "describe-services", "--cluster", "c", "--services", "s"],
            "my-account", _config(FULL),
        ) is None

    def test_insights_query_allowed(self):
        assert check_policy(
            ["logs", "start-query", "--log-group-name", "g",
             "--start-time", "0", "--end-time", "1", "--query-string", "q"],
            "my-account", _config(FULL),
        ) is None

    def test_global_flags_before_service_allowed(self):
        assert check_policy(
            ["--region", "us-east-1", "--output", "json",
             "ecs", "list-tasks", "--cluster", "c"],
            "my-account", _config(FULL),
        ) is None

    def test_write_operation_denied(self):
        reason = check_policy(
            ["ecs", "update-service", "--cluster", "c"],
            "my-account", _config(FULL),
        )
        assert reason is not None
        assert "not in the read-only allowlist" in reason

    def test_unsupported_service_denied(self):
        reason = check_policy(
            ["ssm", "get-parameter", "--name", "x"],
            "my-account", _config(FULL),
        )
        assert reason is not None
        assert "not supported" in reason

    def test_secret_minting_denied(self):
        reason = check_policy(
            ["ecr", "get-login-password"], "my-account", _config(FULL),
        )
        assert reason is not None
        assert "not in the read-only allowlist" in reason

    def test_sqs_not_supported(self):
        reason = check_policy(
            ["sqs", "receive-message", "--queue-url", "u"],
            "my-account", _config(FULL),
        )
        assert reason is not None
        assert "not supported" in reason

    @pytest.mark.parametrize("flag", [
        "--profile", "--endpoint-url", "--debug", "--follow",
        "--with-decryption",
    ])
    def test_denied_anywhere_flags(self, flag):
        reason = check_policy(
            ["logs", "tail", "/my/group", flag], "my-account", _config(FULL),
        )
        assert reason is not None
        assert flag in reason

    def test_denied_flag_equals_form(self):
        reason = check_policy(
            ["--profile=oops", "logs", "tail", "/g"],
            "my-account", _config(FULL),
        )
        assert reason is not None
        assert "--profile" in reason

    def test_unknown_global_flag_denied(self):
        reason = check_policy(
            ["--cli-connect-timeout", "1", "logs", "tail", "/g"],
            "my-account", _config(FULL),
        )
        assert reason is not None
        assert "not allowed before the service" in reason

    def test_bare_service_denied(self):
        reason = check_policy(["logs"], "my-account", _config(FULL))
        assert reason is not None
        assert "expected" in reason

    def test_help_allowed(self):
        assert check_policy(["--help"], "my-account", _config(FULL)) is None
        assert check_policy(["logs", "help"], "my-account", _config(FULL)) is None
        assert check_policy(
            ["ecs", "update-service", "--help"], "my-account", _config(FULL),
        ) is None

    def test_service_not_granted_for_entry(self):
        reason = check_policy(
            ["ecr", "describe-images", "--repository-name", "r"],
            "my-account", _config(["logs"]),
        )
        assert reason is not None
        assert "not granted" in reason

    def test_grants_ride_first_match_wins_routing(self):
        config = {"credentials": [
            {"profile": "a", "resources": ["acct-a"], "services": ["logs"]},
            {"profile": "b", "resources": ["*"], "services": FULL},
        ]}
        assert check_policy(
            ["ecs", "list-tasks"], "acct-a", config,
        ) is not None
        assert check_policy(
            ["ecs", "list-tasks"], "acct-b", config,
        ) is None

    def test_unmatched_resource_falls_through(self):
        assert check_policy(
            ["logs", "tail", "/g"], "no-such-account", _config(FULL),
        ) is None


class TestValidateConfig:
    def _entry(self, **overrides):
        entry = {
            "profile": "readonly",
            "resources": ["my-account"],
            "services": ["logs"],
        }
        entry.update(overrides)
        return entry

    def test_valid_profile_entry(self):
        AwsPlugin().validate_config({"credentials": [self._entry()]})

    def test_valid_key_pair_entry(self):
        entry = self._entry()
        del entry["profile"]
        entry.update(access_key_id="AKIA...", secret_access_key="secret")
        AwsPlugin().validate_config({"credentials": [entry]})

    def test_region_is_optional(self):
        AwsPlugin().validate_config({"credentials": [
            self._entry(region="us-east-1"),
        ]})

    def test_missing_services_rejected(self):
        entry = self._entry()
        del entry["services"]
        with pytest.raises(ConfigError, match="services"):
            AwsPlugin().validate_config({"credentials": [entry]})

    def test_unknown_service_rejected(self):
        with pytest.raises(ConfigError, match="unknown service"):
            AwsPlugin().validate_config({"credentials": [
                self._entry(services=["logs", "s3"]),
            ]})

    def test_empty_services_rejected(self):
        with pytest.raises(ConfigError, match="services"):
            AwsPlugin().validate_config({"credentials": [
                self._entry(services=[]),
            ]})

    def test_profile_and_keys_together_rejected(self):
        with pytest.raises(ConfigError, match="not both"):
            AwsPlugin().validate_config({"credentials": [
                self._entry(access_key_id="AKIA...",
                            secret_access_key="secret"),
            ]})

    def test_no_credential_source_rejected(self):
        entry = self._entry()
        del entry["profile"]
        with pytest.raises(ConfigError, match="profile"):
            AwsPlugin().validate_config({"credentials": [entry]})

    def test_incomplete_key_pair_rejected(self):
        entry = self._entry()
        del entry["profile"]
        entry["access_key_id"] = "AKIA..."
        with pytest.raises(ConfigError, match="secret_access_key"):
            AwsPlugin().validate_config({"credentials": [entry]})

    def test_unknown_key_rejected(self):
        with pytest.raises(ConfigError, match="unknown key"):
            AwsPlugin().validate_config({"credentials": [
                self._entry(acount="typo"),
            ]})

    def test_missing_credentials_key_rejected(self):
        with pytest.raises(ConfigError, match="credentials"):
            AwsPlugin().validate_config({})
