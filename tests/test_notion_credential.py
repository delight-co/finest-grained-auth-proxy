from fgap.plugins.notion.credential import select_credential


class TestSelectCredential:
    def test_wildcard_match(self):
        config = {"credentials": [
            {"token": "ntn_test", "resources": ["*"]},
        ]}
        result = select_credential("default", config)
        assert result["env"]["NOTION_TOKEN"] == "ntn_test"

    def test_first_match_wins(self):
        config = {"credentials": [
            {"token": "ntn_first", "resources": ["workspace-a"]},
            {"token": "ntn_second", "resources": ["*"]},
        ]}
        result = select_credential("workspace-a", config)
        assert result["env"]["NOTION_TOKEN"] == "ntn_first"

    def test_fallback_to_wildcard(self):
        config = {"credentials": [
            {"token": "ntn_specific", "resources": ["workspace-a"]},
            {"token": "ntn_default", "resources": ["*"]},
        ]}
        result = select_credential("workspace-b", config)
        assert result["env"]["NOTION_TOKEN"] == "ntn_default"

    def test_no_match_returns_none(self):
        config = {"credentials": [
            {"token": "ntn_test", "resources": ["workspace-a"]},
        ]}
        assert select_credential("workspace-b", config) is None

    def test_empty_credentials(self):
        assert select_credential("default", {"credentials": []}) is None

    def test_no_credentials_key(self):
        assert select_credential("default", {}) is None

    def test_multiple_resources_per_credential(self):
        config = {"credentials": [
            {"token": "ntn_test", "resources": ["ws-a", "ws-b"]},
        ]}
        assert select_credential("ws-a", config) is not None
        assert select_credential("ws-b", config) is not None
        assert select_credential("ws-c", config) is None
