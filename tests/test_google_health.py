import pytest

from fgap.plugins.google.plugin import GooglePlugin


class TestGoogleHealthCheck:
    async def test_valid_account(self):
        async def fake_run_gog(keyring_pw):
            return {"valid": True, "accounts": "user@example.com"}

        plugin = GooglePlugin()
        config = {"credentials": [
            {"keyring_password": "test-password-123", "resources": ["*"]},
        ]}
        results = await plugin.health_check(config, _run_gog=fake_run_gog)

        assert len(results) == 1
        r = results[0]
        assert r["valid"] is True
        assert r["accounts"] == "user@example.com"
        assert r["masked_keyring_password"] == "test***"
        assert r["resources"] == ["*"]

    async def test_invalid_keyring(self):
        async def fake_run_gog(keyring_pw):
            return {"valid": False, "error": "invalid keyring password"}

        plugin = GooglePlugin()
        config = {"credentials": [
            {"keyring_password": "wrong-password-123", "resources": ["*"]},
        ]}
        results = await plugin.health_check(config, _run_gog=fake_run_gog)

        assert len(results) == 1
        r = results[0]
        assert r["valid"] is False
        assert "invalid keyring" in r["error"]

    async def test_gog_not_installed(self):
        """Without DI override, gog binary is not found in test env."""
        plugin = GooglePlugin()
        config = {"credentials": [
            {"keyring_password": "test-password-123", "resources": ["*"]},
        ]}
        results = await plugin.health_check(config)

        assert len(results) == 1
        r = results[0]
        assert r["valid"] is False
        assert "not found" in r["error"]

    async def test_multiple_credentials(self):
        call_count = 0

        async def fake_run_gog(keyring_pw):
            nonlocal call_count
            call_count += 1
            return {"valid": True, "accounts": f"user{call_count}@example.com"}

        plugin = GooglePlugin()
        config = {"credentials": [
            {"keyring_password": "pw1_xxxxx", "resources": ["user1@example.com"]},
            {"keyring_password": "pw2_xxxxx", "resources": ["user2@example.com"]},
        ]}
        results = await plugin.health_check(config, _run_gog=fake_run_gog)

        assert len(results) == 2
        assert results[0]["accounts"] == "user1@example.com"
        assert results[1]["accounts"] == "user2@example.com"

    async def test_empty_credentials(self):
        plugin = GooglePlugin()
        results = await plugin.health_check({"credentials": []})
        assert results == []

    async def test_short_password_fully_masked(self):
        async def fake_run_gog(keyring_pw):
            return {"valid": True, "accounts": "user@example.com"}

        plugin = GooglePlugin()
        config = {"credentials": [
            {"keyring_password": "ab", "resources": ["*"]},
        ]}
        results = await plugin.health_check(config, _run_gog=fake_run_gog)
        assert results[0]["masked_keyring_password"] == "***"

    async def test_run_gog_exception(self):
        async def failing_run_gog(keyring_pw):
            raise RuntimeError("subprocess boom")

        plugin = GooglePlugin()
        config = {"credentials": [
            {"keyring_password": "test-password-123", "resources": ["*"]},
        ]}
        results = await plugin.health_check(config, _run_gog=failing_run_gog)

        assert len(results) == 1
        r = results[0]
        assert r["valid"] is False
        assert "subprocess boom" in r["error"]
