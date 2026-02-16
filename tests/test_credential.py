from fgap.core.credential import select_credential


class TestSelectCredential:
    def test_exact_match(self, echo_plugin, echo_config):
        plugins = {"echo": echo_plugin}
        result = select_credential("echo", "acme/repo1", echo_config, plugins)
        assert result == {"env": {"ECHO_TOKEN": "tok_specific"}}

    def test_wildcard_match(self, echo_plugin, echo_config):
        plugins = {"echo": echo_plugin}
        result = select_credential("echo", "acme/repo2", echo_config, plugins)
        assert result == {"env": {"ECHO_TOKEN": "tok_wildcard"}}

    def test_star_match(self, echo_plugin, echo_config):
        plugins = {"echo": echo_plugin}
        result = select_credential("echo", "other/repo", echo_config, plugins)
        assert result == {"env": {"ECHO_TOKEN": "tok_default"}}

    def test_first_match_wins(self, echo_plugin, echo_config):
        """acme/repo1 matches both exact and wildcard; exact comes first."""
        plugins = {"echo": echo_plugin}
        result = select_credential("echo", "acme/repo1", echo_config, plugins)
        assert result["env"]["ECHO_TOKEN"] == "tok_specific"

    def test_no_plugin_for_tool(self, echo_plugin, echo_config):
        plugins = {"echo": echo_plugin}
        result = select_credential("unknown_tool", "acme/repo1", echo_config, plugins)
        assert result is None

    def test_no_credential_match(self, echo_plugin):
        plugins = {"echo": echo_plugin}
        config = {"plugins": {"echo": {"credentials": [
            {"token": "tok", "resources": ["specific/only"]},
        ]}}}
        result = select_credential("echo", "other/repo", config, plugins)
        assert result is None

    def test_empty_credentials(self, echo_plugin):
        plugins = {"echo": echo_plugin}
        config = {"plugins": {"echo": {"credentials": []}}}
        result = select_credential("echo", "any/repo", config, plugins)
        assert result is None
