from fgap.client.fly import (
    DEFAULT_MINT_EXPIRY,
    LOCAL_COMMANDS,
    extract_app,
    extract_expiry,
)


class TestExtractApp:
    def test_short_flag(self):
        assert extract_app(["status", "-a", "my-app"]) == "my-app"

    def test_long_flag(self):
        assert extract_app(["status", "--app", "my-app"]) == "my-app"

    def test_equals_forms(self):
        assert extract_app(["status", "--app=my-app"]) == "my-app"
        assert extract_app(["status", "-a=my-app"]) == "my-app"

    def test_env_fallback(self, tmp_path):
        app = extract_app(["status"], environ={"FLY_APP": "env-app"},
                          toml_path=str(tmp_path / "fly.toml"))
        assert app == "env-app"

    def test_fly_toml_fallback(self, tmp_path):
        toml = tmp_path / "fly.toml"
        toml.write_text('# my app\napp = "toml-app"\nprimary_region = "nrt"\n',
                        encoding="utf-8")
        assert extract_app(["status"], environ={},
                           toml_path=str(toml)) == "toml-app"

    def test_fly_toml_unquoted(self, tmp_path):
        toml = tmp_path / "fly.toml"
        toml.write_text("app = bare-app\n", encoding="utf-8")
        assert extract_app(["status"], environ={},
                           toml_path=str(toml)) == "bare-app"

    def test_flag_beats_env_and_toml(self, tmp_path):
        toml = tmp_path / "fly.toml"
        toml.write_text('app = "toml-app"\n', encoding="utf-8")
        app = extract_app(["-a", "flag-app"],
                          environ={"FLY_APP": "env-app"},
                          toml_path=str(toml))
        assert app == "flag-app"

    def test_nothing_found(self, tmp_path):
        assert extract_app(["status"], environ={},
                           toml_path=str(tmp_path / "fly.toml")) == ""


class TestExtractExpiry:
    def test_default(self):
        args, expiry = extract_expiry(["deploy", "--remote-only"])
        assert args == ["deploy", "--remote-only"]
        assert expiry == DEFAULT_MINT_EXPIRY

    def test_flag_is_stripped(self):
        args, expiry = extract_expiry(
            ["deploy", "--fgap-expiry", "30m", "--remote-only"])
        assert args == ["deploy", "--remote-only"]
        assert expiry == "30m"

    def test_equals_form(self):
        args, expiry = extract_expiry(["deploy", "--fgap-expiry=1h"])
        assert args == ["deploy"]
        assert expiry == "1h"


class TestLocalCommands:
    def test_context_and_streaming_commands_are_local(self):
        # local working directory (build context) or live connections:
        # the buffered /cli round-trip cannot carry these
        for cmd in ("deploy", "logs", "ssh"):
            assert cmd in LOCAL_COMMANDS

    def test_api_commands_stay_on_the_proxy(self):
        for cmd in ("status", "machines", "secrets", "scale", "releases"):
            assert cmd not in LOCAL_COMMANDS
