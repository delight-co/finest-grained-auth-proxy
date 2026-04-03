from fgap.plugins.langfuse.credential import select_credential


class TestSelectCredential:
    def test_wildcard_match(self):
        config = {"credentials": [
            {
                "public_key": "pk-lf-test",
                "secret_key": "sk-lf-test",
                "host": "https://us.cloud.langfuse.com",
                "resources": ["*"],
            },
        ]}
        result = select_credential("default", config)
        assert result == {"env": {
            "LANGFUSE_PUBLIC_KEY": "pk-lf-test",
            "LANGFUSE_SECRET_KEY": "sk-lf-test",
            "LANGFUSE_BASE_URL": "https://us.cloud.langfuse.com",
        }}

    def test_host_optional(self):
        config = {"credentials": [
            {
                "public_key": "pk-lf-test",
                "secret_key": "sk-lf-test",
                "resources": ["*"],
            },
        ]}
        result = select_credential("default", config)
        assert result == {"env": {
            "LANGFUSE_PUBLIC_KEY": "pk-lf-test",
            "LANGFUSE_SECRET_KEY": "sk-lf-test",
        }}

    def test_first_match_wins(self):
        config = {"credentials": [
            {
                "public_key": "pk-first",
                "secret_key": "sk-first",
                "resources": ["project-a"],
            },
            {
                "public_key": "pk-second",
                "secret_key": "sk-second",
                "resources": ["*"],
            },
        ]}
        result = select_credential("project-a", config)
        assert result["env"]["LANGFUSE_PUBLIC_KEY"] == "pk-first"

    def test_no_match_returns_none(self):
        config = {"credentials": [
            {
                "public_key": "pk-test",
                "secret_key": "sk-test",
                "resources": ["project-a"],
            },
        ]}
        assert select_credential("project-b", config) is None

    def test_empty_credentials(self):
        assert select_credential("default", {"credentials": []}) is None

    def test_no_credentials_key(self):
        assert select_credential("default", {}) is None
