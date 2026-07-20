import pytest
from aiohttp.test_utils import TestClient, TestServer

from fgap.core.config import ConfigError
from fgap.core.router import create_routes
from fgap.plugins.langfuse.plugin import LangfusePlugin


class TestStartupValidation:
    def test_unknown_plugin_section_rejected(self):
        with pytest.raises(ConfigError, match="no such plugin"):
            create_routes({"plugins": {"nope": {}}}, {})

    def test_strict_plugin_validates_its_section(self):
        config = {"plugins": {"langfuse": {"credentials": [
            {"public_key": "pk-lf-x", "secret_key": "sk-lf-x",
             "resources": ["proj"]},
        ]}}}
        with pytest.raises(ConfigError, match="permissions"):
            create_routes(config, {"langfuse": LangfusePlugin()})

    def test_plugin_without_section_is_fine(self):
        create_routes({}, {"langfuse": LangfusePlugin()})


@pytest.fixture
async def langfuse_readonly_client():
    config = {"plugins": {"langfuse": {"credentials": [
        {"public_key": "pk-lf-x", "secret_key": "sk-lf-x",
         "resources": ["proj"], "permissions": ["read"]},
    ]}}}
    app = create_routes(config, {"langfuse": LangfusePlugin()})
    async with TestClient(TestServer(app)) as client:
        yield client


class TestPolicyEnforcement:
    async def test_deny_becomes_403_with_reason(self, langfuse_readonly_client):
        resp = await langfuse_readonly_client.post("/cli", json={
            "tool": "langfuse",
            "args": ["api", "prompts", "create"],
            "resource": "proj",
        })
        assert resp.status == 403
        text = await resp.text()
        assert "Policy denied" in text
        assert "'write' permission is not granted" in text

    async def test_unknown_verb_403(self, langfuse_readonly_client):
        resp = await langfuse_readonly_client.post("/cli", json={
            "tool": "langfuse",
            "args": ["api", "traces", "truncate"],
            "resource": "proj",
        })
        assert resp.status == 403
        assert "unrecognized" in await resp.text()
