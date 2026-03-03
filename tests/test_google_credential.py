import os

from fgap.plugins.google.credential import (
    _provisioned_sa_keys,
    select_credential,
)


class TestSelectCredential:
    def test_first_match_wins(self):
        config = {"credentials": [
            {"keyring_password": "pw_specific", "resources": ["user@example.com"]},
            {"keyring_password": "pw_default", "resources": ["*"]},
        ]}
        result = select_credential("user@example.com", config)
        assert result["env"]["GOG_KEYRING_PASSWORD"] == "pw_specific"

    def test_wildcard_match(self):
        config = {"credentials": [
            {"keyring_password": "pw_specific", "resources": ["user@example.com"]},
            {"keyring_password": "pw_default", "resources": ["*"]},
        ]}
        result = select_credential("other@example.com", config)
        assert result["env"]["GOG_KEYRING_PASSWORD"] == "pw_default"

    def test_default_resource(self):
        config = {"credentials": [
            {"keyring_password": "pw", "resources": ["default"]},
        ]}
        result = select_credential("default", config)
        assert result["env"]["GOG_KEYRING_PASSWORD"] == "pw"

    def test_no_match_returns_none(self):
        config = {"credentials": [
            {"keyring_password": "pw", "resources": ["user@example.com"]},
        ]}
        assert select_credential("other@example.com", config) is None

    def test_empty_credentials(self):
        assert select_credential("default", {"credentials": []}) is None

    def test_no_credentials_key(self):
        assert select_credential("default", {}) is None

    def test_includes_account_when_specified(self):
        config = {"credentials": [
            {
                "keyring_password": "pw",
                "account": "user@example.com",
                "resources": ["*"],
            },
        ]}
        result = select_credential("default", config)
        assert result["env"]["GOG_KEYRING_PASSWORD"] == "pw"
        assert result["env"]["GOG_ACCOUNT"] == "user@example.com"

    def test_no_account_field(self):
        config = {"credentials": [
            {"keyring_password": "pw", "resources": ["*"]},
        ]}
        result = select_credential("default", config)
        assert "GOG_ACCOUNT" not in result["env"]

    def test_multiple_resources_per_credential(self):
        config = {"credentials": [
            {"keyring_password": "pw", "resources": ["user1@example.com", "user2@example.com"]},
        ]}
        assert select_credential("user1@example.com", config) is not None
        assert select_credential("user2@example.com", config) is not None
        assert select_credential("user3@example.com", config) is None

    def test_skips_credential_without_keyring_password(self):
        config = {"credentials": [
            {"resources": ["*"]},
            {"keyring_password": "pw", "resources": ["*"]},
        ]}
        result = select_credential("default", config)
        assert result["env"]["GOG_KEYRING_PASSWORD"] == "pw"


class TestSelectCredentialSA:
    def test_sa_credential_returns_account(self, tmp_path):
        _provisioned_sa_keys.clear()
        sa_key = tmp_path / "sa.json"
        sa_key.write_text('{"type":"service_account"}')
        config = {"credentials": [
            {
                "sa_key_file": str(sa_key),
                "account": "sa@proj.iam.gserviceaccount.com",
                "resources": ["*"],
            },
        ]}
        result = select_credential("default", config)
        assert result["env"]["GOG_ACCOUNT"] == "sa@proj.iam.gserviceaccount.com"
        assert "GOG_KEYRING_PASSWORD" not in result["env"]

    def test_sa_provisions_key_to_gog_path(self, tmp_path, monkeypatch):
        _provisioned_sa_keys.clear()
        sa_key = tmp_path / "sa.json"
        sa_key.write_text('{"type":"service_account","private_key":"xxx"}')
        monkeypatch.setenv("HOME", str(tmp_path))
        config = {"credentials": [
            {
                "sa_key_file": str(sa_key),
                "account": "sa@proj.iam.gserviceaccount.com",
                "resources": ["*"],
            },
        ]}
        select_credential("default", config)
        # Check gog's expected path exists
        import base64
        encoded = base64.urlsafe_b64encode(
            b"sa@proj.iam.gserviceaccount.com",
        ).decode().rstrip("=")
        expected = tmp_path / ".config" / "gogcli" / f"sa-{encoded}.json"
        assert expected.exists()
        assert expected.read_text() == sa_key.read_text()
        assert oct(expected.stat().st_mode)[-3:] == "600"

    def test_sa_preferred_over_oauth(self, tmp_path):
        _provisioned_sa_keys.clear()
        sa_key = tmp_path / "sa.json"
        sa_key.write_text('{"type":"service_account"}')
        config = {"credentials": [
            {
                "sa_key_file": str(sa_key),
                "account": "sa@proj.iam.gserviceaccount.com",
                "resources": ["*"],
            },
            {"keyring_password": "pw", "resources": ["*"]},
        ]}
        result = select_credential("default", config)
        assert "GOG_ACCOUNT" in result["env"]
        assert "GOG_KEYRING_PASSWORD" not in result["env"]

    def test_sa_no_match_falls_through_to_oauth(self, tmp_path):
        _provisioned_sa_keys.clear()
        sa_key = tmp_path / "sa.json"
        sa_key.write_text('{"type":"service_account"}')
        config = {"credentials": [
            {
                "sa_key_file": str(sa_key),
                "account": "sa@proj.iam.gserviceaccount.com",
                "resources": ["specific@example.com"],
            },
            {"keyring_password": "pw", "resources": ["*"]},
        ]}
        result = select_credential("default", config)
        assert result["env"]["GOG_KEYRING_PASSWORD"] == "pw"

    def test_skips_credential_without_keyring_or_sa(self):
        config = {"credentials": [
            {"account": "user@example.com", "resources": ["*"]},
            {"keyring_password": "pw", "resources": ["*"]},
        ]}
        result = select_credential("default", config)
        assert result["env"]["GOG_KEYRING_PASSWORD"] == "pw"
