from fgap.plugins.github.credential import match_resource, select_credential


class TestMatchResource:
    def test_star_matches_all(self):
        assert match_resource("*", "any/repo")

    def test_owner_wildcard(self):
        assert match_resource("acme/*", "acme/repo1")
        assert match_resource("acme/*", "acme/anything")
        assert not match_resource("acme/*", "other/repo")

    def test_exact_match(self):
        assert match_resource("acme/repo1", "acme/repo1")
        assert not match_resource("acme/repo1", "acme/repo2")

    def test_case_insensitive(self):
        assert match_resource("Acme/*", "acme/repo")
        assert match_resource("acme/Repo", "Acme/repo")

    def test_fnmatch_single_char(self):
        assert match_resource("acme/repo-?", "acme/repo-1")
        assert not match_resource("acme/repo-?", "acme/repo-12")

    def test_fnmatch_bracket(self):
        assert match_resource("acme/repo-[abc]", "acme/repo-a")
        assert not match_resource("acme/repo-[abc]", "acme/repo-d")


class TestSelectCredential:
    def test_first_match_wins(self):
        config = {"credentials": [
            {"token": "tok_specific", "resources": ["acme/repo1"]},
            {"token": "tok_wildcard", "resources": ["acme/*"]},
            {"token": "tok_default", "resources": ["*"]},
        ]}
        result = select_credential("acme/repo1", config)
        assert result["env"]["GH_TOKEN"] == "tok_specific"

    def test_wildcard_match(self):
        config = {"credentials": [
            {"token": "tok_specific", "resources": ["acme/repo1"]},
            {"token": "tok_wildcard", "resources": ["acme/*"]},
        ]}
        result = select_credential("acme/repo2", config)
        assert result["env"]["GH_TOKEN"] == "tok_wildcard"

    def test_star_fallback(self):
        config = {"credentials": [
            {"token": "tok_default", "resources": ["*"]},
        ]}
        result = select_credential("any/repo", config)
        assert result["env"]["GH_TOKEN"] == "tok_default"

    def test_no_match_returns_none(self):
        config = {"credentials": [
            {"token": "tok", "resources": ["acme/*"]},
        ]}
        assert select_credential("other/repo", config) is None

    def test_empty_credentials(self):
        assert select_credential("any/repo", {"credentials": []}) is None

    def test_no_credentials_key(self):
        assert select_credential("any/repo", {}) is None

    def test_includes_gh_host(self):
        config = {"credentials": [{"token": "t", "resources": ["*"]}]}
        result = select_credential("any/repo", config)
        assert result["env"]["GH_HOST"] == "github.com"

    def test_multiple_resources_per_credential(self):
        config = {"credentials": [
            {"token": "tok", "resources": ["acme/repo1", "acme/repo2"]},
        ]}
        assert select_credential("acme/repo1", config) is not None
        assert select_credential("acme/repo2", config) is not None
        assert select_credential("acme/repo3", config) is None
