import pytest

from idfm_transit_mcp.config import Settings


def test_secret_file_overrides_environment_without_repr_leak(monkeypatch, tmp_path):
    secret = tmp_path / "key"
    secret.write_text("file-test-secret\n", encoding="utf-8")
    monkeypatch.setenv("IDFM_API_KEY", "environment-test-secret")
    monkeypatch.setenv("IDFM_API_KEY_FILE", str(secret))
    settings = Settings.from_env()
    assert settings.api_key.get_secret_value() == "file-test-secret"
    assert "file-test-secret" not in repr(settings)


def test_unreadable_secret_file_has_safe_error(monkeypatch):
    monkeypatch.setenv("IDFM_API_KEY_FILE", "/this-file-does-not-exist")
    with pytest.raises(ValueError, match="check path and permissions"):
        Settings.from_env()
