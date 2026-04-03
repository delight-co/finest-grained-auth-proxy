from unittest.mock import AsyncMock, patch, MagicMock
from aiohttp import ClientSession

from fgap.plugins.langfuse.plugin import LangfusePlugin, _check_credentials


class TestLangfuseHealthCheck:
    async def test_valid_credentials(self):
        plugin = LangfusePlugin()
        config = {"credentials": [
            {
                "public_key": "pk-lf-abc123test",
                "secret_key": "sk-lf-secret456",
                "host": "https://us.cloud.langfuse.com",
                "resources": ["*"],
            },
        ]}

        mock_status = {"valid": True, "project": "my-project"}
        with patch(
            "fgap.plugins.langfuse.plugin._check_credentials",
            new_callable=AsyncMock,
            return_value=mock_status,
        ):
            results = await plugin.health_check(config)

        assert len(results) == 1
        assert results[0]["valid"] is True
        assert results[0]["project"] == "my-project"
        assert results[0]["host"] == "https://us.cloud.langfuse.com"
        assert results[0]["resources"] == ["*"]

    async def test_invalid_credentials(self):
        plugin = LangfusePlugin()
        config = {"credentials": [
            {
                "public_key": "pk-lf-bad",
                "secret_key": "sk-lf-bad",
                "resources": ["*"],
            },
        ]}

        mock_status = {"valid": False, "error": "HTTP 401: Unauthorized"}
        with patch(
            "fgap.plugins.langfuse.plugin._check_credentials",
            new_callable=AsyncMock,
            return_value=mock_status,
        ):
            results = await plugin.health_check(config)

        assert len(results) == 1
        assert results[0]["valid"] is False
        assert "401" in results[0]["error"]

    async def test_connection_error(self):
        plugin = LangfusePlugin()
        config = {"credentials": [
            {
                "public_key": "pk-lf-test1234",
                "secret_key": "sk-lf-test1234",
                "resources": ["*"],
            },
        ]}

        with patch(
            "fgap.plugins.langfuse.plugin._check_credentials",
            new_callable=AsyncMock,
            side_effect=ConnectionError("refused"),
        ):
            results = await plugin.health_check(config)

        assert len(results) == 1
        assert results[0]["valid"] is False
        assert "refused" in results[0]["error"]

    async def test_host_defaults_when_absent(self):
        plugin = LangfusePlugin()
        config = {"credentials": [
            {"public_key": "pk", "secret_key": "sk", "resources": ["*"]},
        ]}

        with patch(
            "fgap.plugins.langfuse.plugin._check_credentials",
            new_callable=AsyncMock,
            return_value={"valid": True, "project": ""},
        ):
            results = await plugin.health_check(config)

        assert results[0]["host"] == "https://cloud.langfuse.com"

    async def test_masked_keys(self):
        plugin = LangfusePlugin()
        config = {"credentials": [
            {
                "public_key": "pk-lf-abcdefghijk",
                "secret_key": "sk-lf-secretkey123",
                "resources": ["*"],
            },
        ]}

        with patch(
            "fgap.plugins.langfuse.plugin._check_credentials",
            new_callable=AsyncMock,
            return_value={"valid": True, "project": "test"},
        ):
            results = await plugin.health_check(config)

        assert "pk-lf-abcdefghijk" not in results[0]["masked_public_key"]
        assert results[0]["masked_public_key"].startswith("pk-lf-ab")

    async def test_empty_credentials(self):
        plugin = LangfusePlugin()
        results = await plugin.health_check({"credentials": []})
        assert results == []

    async def test_multiple_credentials(self):
        plugin = LangfusePlugin()
        config = {"credentials": [
            {"public_key": "pk1", "secret_key": "sk1", "resources": ["proj-a"]},
            {"public_key": "pk2", "secret_key": "sk2", "host": "https://self-hosted.example.com", "resources": ["*"]},
        ]}

        with patch(
            "fgap.plugins.langfuse.plugin._check_credentials",
            new_callable=AsyncMock,
            return_value={"valid": True, "project": "test"},
        ):
            results = await plugin.health_check(config)

        assert len(results) == 2
        assert results[1]["host"] == "https://self-hosted.example.com"

    async def test_api_url_override(self):
        """The _api_url parameter overrides host for testing."""
        plugin = LangfusePlugin()
        config = {"credentials": [
            {
                "public_key": "pk-test",
                "secret_key": "sk-test",
                "host": "https://cloud.langfuse.com",
                "resources": ["*"],
            },
        ]}

        with patch(
            "fgap.plugins.langfuse.plugin._check_credentials",
            new_callable=AsyncMock,
            return_value={"valid": True, "project": "test"},
        ) as mock_check:
            await plugin.health_check(config, _api_url="http://localhost:3000")
            mock_check.assert_called_once_with(
                "pk-test", "sk-test", "http://localhost:3000",
            )
