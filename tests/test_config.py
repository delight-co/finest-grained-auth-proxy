import os

import pytest

from fgap.core.config import ConfigError, load_config


class TestLoadConfig:
    def test_valid_config(self, tmp_path):
        f = tmp_path / "c.json5"
        f.write_text('{"port": 8766, "plugins": {"gh": {"credentials": [{"token": "t", "resources": ["*"]}]}}}')
        os.chmod(f, 0o600)
        config = load_config(str(f))
        assert config["port"] == 8766
        assert "gh" in config["plugins"]

    def test_file_not_found(self):
        with pytest.raises(ConfigError, match="not found"):
            load_config("/nonexistent/config.json5")

    def test_permissions_too_open_644(self, tmp_path):
        f = tmp_path / "c.json5"
        f.write_text("{}")
        os.chmod(f, 0o644)
        with pytest.raises(ConfigError, match="too-open permissions"):
            load_config(str(f))

    def test_permissions_too_open_640(self, tmp_path):
        f = tmp_path / "c.json5"
        f.write_text("{}")
        os.chmod(f, 0o640)
        with pytest.raises(ConfigError, match="too-open permissions"):
            load_config(str(f))

    def test_permissions_600_ok(self, tmp_path):
        f = tmp_path / "c.json5"
        f.write_text("{}")
        os.chmod(f, 0o600)
        load_config(str(f))  # should not raise

    def test_invalid_json5(self, tmp_path):
        f = tmp_path / "c.json5"
        f.write_text("{not valid json5 at all")
        os.chmod(f, 0o600)
        with pytest.raises(ConfigError, match="Invalid JSON5"):
            load_config(str(f))

    def test_config_must_be_object(self, tmp_path):
        f = tmp_path / "c.json5"
        f.write_text('["array"]')
        os.chmod(f, 0o600)
        with pytest.raises(ConfigError, match="must be a JSON object"):
            load_config(str(f))

    def test_plugins_must_be_object(self, tmp_path):
        f = tmp_path / "c.json5"
        f.write_text('{"plugins": "bad"}')
        os.chmod(f, 0o600)
        with pytest.raises(ConfigError, match="'plugins' must be an object"):
            load_config(str(f))

    def test_credential_missing_resources(self, tmp_path):
        f = tmp_path / "c.json5"
        f.write_text('{"plugins": {"gh": {"credentials": [{"token": "t"}]}}}')
        os.chmod(f, 0o600)
        with pytest.raises(ConfigError, match="missing 'resources'"):
            load_config(str(f))

    def test_no_plugins_section_is_valid(self, tmp_path):
        f = tmp_path / "c.json5"
        f.write_text('{"port": 9000}')
        os.chmod(f, 0o600)
        config = load_config(str(f))
        assert config["port"] == 9000

    def test_empty_credentials_is_valid(self, tmp_path):
        f = tmp_path / "c.json5"
        f.write_text('{"plugins": {"gh": {"credentials": []}}}')
        os.chmod(f, 0o600)
        assert load_config(str(f))["plugins"]["gh"]["credentials"] == []

    def test_json5_comments_accepted(self, tmp_path):
        f = tmp_path / "c.json5"
        f.write_text('{\n  // comment\n  "port": 1234\n}')
        os.chmod(f, 0o600)
        assert load_config(str(f))["port"] == 1234

    def test_json5_trailing_comma_accepted(self, tmp_path):
        f = tmp_path / "c.json5"
        f.write_text('{"port": 1234,}')
        os.chmod(f, 0o600)
        assert load_config(str(f))["port"] == 1234
