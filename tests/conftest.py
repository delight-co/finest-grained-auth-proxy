import pytest

from fgap.plugins.base import Plugin


class EchoPlugin(Plugin):
    """Test plugin that maps to the real 'echo' binary."""

    @property
    def name(self) -> str:
        return "echo"

    @property
    def tools(self) -> list[str]:
        return ["echo"]

    def select_credential(self, resource: str, config: dict) -> dict | None:
        for cred in config.get("credentials", []):
            for pattern in cred.get("resources", []):
                if _match_resource(pattern, resource):
                    return {"env": {"ECHO_TOKEN": cred["token"]}}
        return None


class FallthroughPlugin(Plugin):
    """Test plugin with a custom command that can fall through."""

    @property
    def name(self) -> str:
        return "ft"

    @property
    def tools(self) -> list[str]:
        return ["printf"]

    def select_credential(self, resource: str, config: dict) -> dict | None:
        for cred in config.get("credentials", []):
            for pattern in cred.get("resources", []):
                if pattern == "*" or pattern == resource:
                    return {"env": {"FT_TOKEN": cred["token"]}}
        return None

    def get_commands(self) -> dict:
        async def handle_custom(args, resource, credential):
            if args and args[0] == "intercept":
                return {"exit_code": 0, "stdout": "intercepted", "stderr": ""}
            return None  # fall through to CLI

        return {"custom": handle_custom}


def _match_resource(pattern: str, resource: str) -> bool:
    if pattern == "*":
        return True
    if pattern.endswith("/*"):
        return resource.split("/")[0] == pattern[:-2]
    return pattern == resource


@pytest.fixture
def echo_plugin():
    return EchoPlugin()


@pytest.fixture
def echo_config():
    return {
        "plugins": {
            "echo": {
                "credentials": [
                    {"token": "tok_specific", "resources": ["acme/repo1"]},
                    {"token": "tok_wildcard", "resources": ["acme/*"]},
                    {"token": "tok_default", "resources": ["*"]},
                ]
            }
        }
    }


@pytest.fixture
def ft_plugin():
    return FallthroughPlugin()


@pytest.fixture
def ft_config():
    return {
        "plugins": {
            "ft": {
                "credentials": [
                    {"token": "ft_tok", "resources": ["*"]},
                ]
            }
        }
    }
