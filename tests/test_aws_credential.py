from fgap.plugins.aws.credential import select_credential


class TestSelectCredential:
    def test_profile_entry(self):
        config = {"credentials": [
            {"profile": "readonly", "region": "us-east-1",
             "resources": ["my-account"], "services": ["logs"]},
        ]}
        result = select_credential("my-account", config)
        assert result == {"env": {
            "AWS_PROFILE": "readonly",
            "AWS_DEFAULT_REGION": "us-east-1",
            "AWS_PAGER": "",
        }}

    def test_key_pair_entry(self):
        config = {"credentials": [
            {"access_key_id": "AKIAEXAMPLE", "secret_access_key": "secret",
             "resources": ["my-account"], "services": ["logs"]},
        ]}
        result = select_credential("my-account", config)
        assert result["env"]["AWS_ACCESS_KEY_ID"] == "AKIAEXAMPLE"
        assert result["env"]["AWS_SECRET_ACCESS_KEY"] == "secret"
        assert result["env"]["AWS_PAGER"] == ""
        assert "AWS_DEFAULT_REGION" not in result["env"]

    def test_first_match_wins(self):
        config = {"credentials": [
            {"profile": "a", "resources": ["acct-a"], "services": ["logs"]},
            {"profile": "b", "resources": ["*"], "services": ["logs"]},
        ]}
        assert select_credential("acct-a", config)["env"]["AWS_PROFILE"] == "a"
        assert select_credential("other", config)["env"]["AWS_PROFILE"] == "b"

    def test_no_match_returns_none(self):
        config = {"credentials": [
            {"profile": "a", "resources": ["acct-a"], "services": ["logs"]},
        ]}
        assert select_credential("nope", config) is None
