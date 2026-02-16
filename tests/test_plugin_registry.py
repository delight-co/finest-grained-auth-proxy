import pytest

from fgap.plugins import clear_registry, discover_plugins, register_plugin
from fgap.plugins.base import Plugin


class DummyPlugin(Plugin):
    @property
    def name(self) -> str:
        return "dummy"

    @property
    def tools(self) -> list[str]:
        return ["dummy_tool"]

    def select_credential(self, resource: str, config: dict) -> dict | None:
        return None


class AnotherPlugin(Plugin):
    @property
    def name(self) -> str:
        return "another"

    @property
    def tools(self) -> list[str]:
        return ["another_tool"]

    def select_credential(self, resource: str, config: dict) -> dict | None:
        return None


class TestPluginRegistry:
    def setup_method(self):
        clear_registry()

    def teardown_method(self):
        clear_registry()

    def test_register_and_discover(self):
        register_plugin(DummyPlugin)
        config = {"plugins": {"dummy": {"credentials": []}}}
        plugins = discover_plugins(config)
        assert "dummy" in plugins
        assert isinstance(plugins["dummy"], DummyPlugin)

    def test_discover_only_configured_plugins(self):
        register_plugin(DummyPlugin)
        register_plugin(AnotherPlugin)
        config = {"plugins": {"dummy": {"credentials": []}}}
        plugins = discover_plugins(config)
        assert "dummy" in plugins
        assert "another" not in plugins

    def test_same_class_registration_is_idempotent(self):
        register_plugin(DummyPlugin)
        register_plugin(DummyPlugin)  # should not raise

    def test_name_conflict_with_different_class_raises(self):
        register_plugin(DummyPlugin)

        class ConflictPlugin(Plugin):
            @property
            def name(self):
                return "dummy"

            @property
            def tools(self):
                return ["conflict"]

            def select_credential(self, resource, config):
                return None

        with pytest.raises(ValueError, match="already registered"):
            register_plugin(ConflictPlugin)

    def test_discover_with_empty_config(self):
        register_plugin(DummyPlugin)
        plugins = discover_plugins({"plugins": {}})
        assert len(plugins) == 0

    def test_discover_with_no_plugins_section(self):
        register_plugin(DummyPlugin)
        plugins = discover_plugins({})
        assert len(plugins) == 0
