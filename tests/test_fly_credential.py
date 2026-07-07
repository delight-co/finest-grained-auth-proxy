from fgap.plugins.fly.credential import select_credential


class TestSelectCredential:
    def test_exact_app_match(self):
        config = {"credentials": [
            {"token": "FlyV1 fm2_aaa", "resources": ["my-app"]},
        ]}
        cred = select_credential("my-app", config)
        assert cred == {"env": {
            "FLY_API_TOKEN": "FlyV1 fm2_aaa",
            "FLY_NO_UPDATE_CHECK": "1",
        }}

    def test_fnmatch_app_family(self):
        config = {"credentials": [
            {"token": "FlyV1 fm2_aaa", "resources": ["my-app-*"]},
        ]}
        assert select_credential("my-app-staging", config) is not None
        assert select_credential("other-app", config) is None

    def test_wildcard(self):
        config = {"credentials": [
            {"token": "FlyV1 fm2_aaa", "resources": ["*"]},
        ]}
        assert select_credential("anything", config) is not None

    def test_first_match_wins(self):
        config = {"credentials": [
            {"token": "FlyV1 fm2_scoped", "resources": ["my-app"]},
            {"token": "FlyV1 fm2_wide", "resources": ["*"]},
        ]}
        cred = select_credential("my-app", config)
        assert cred["env"]["FLY_API_TOKEN"] == "FlyV1 fm2_scoped"

    def test_no_match(self):
        config = {"credentials": [
            {"token": "FlyV1 fm2_aaa", "resources": ["my-app"]},
        ]}
        assert select_credential("unrelated", config) is None

    def test_empty_config(self):
        assert select_credential("my-app", {}) is None
