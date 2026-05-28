"""Integration tests for spec 018 — User Story 3 (Vault is the source of truth).

Covers FR-002, FR-003, FR-010 without spinning up a real Vault container — the
`hvac.Client` calls inside `core.vault.fetch_service_token` are patched at the
seams.

What we verify:
- Non-local `APP_ENV` invokes Vault and a failure aborts startup.
- Local `APP_ENV` falls back to `.env` and logs a warning.
- Rotating the value in Vault is picked up after a settings cache reset.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from app.core import config as backend_config
from app.core.vault import VaultUnavailable
from tests.integration.conftest import TEST_SERVICE_TOKEN


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> None:
    backend_config.get_settings.cache_clear()
    yield
    backend_config.get_settings.cache_clear()


def _hvac_stub_returning(secret_value: str) -> MagicMock:
    """Build a MagicMock that imitates `hvac.Client` returning `secret_value`."""
    client = MagicMock()
    client.is_authenticated.return_value = True
    client.secrets.kv.v2.read_secret_version.return_value = {
        "data": {"data": {"token": secret_value}}
    }
    return client


def test_local_mode_allows_env_fallback_and_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """APP_ENV=local + empty SERVICE_AUTH_SECRET emits a warning, no Vault call."""
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("SERVICE_AUTH_SECRET", "")

    with patch("app.core.config.fetch_service_token") as fetch:
        with caplog.at_level(logging.WARNING, logger="app.core.config"):
            settings = backend_config.get_settings()
    fetch.assert_not_called()
    assert settings.SERVICE_AUTH_SECRET == ""
    assert any(
        "Vault not consulted" in rec.message for rec in caplog.records
    ), "Expected a warning when local mode runs without a token"


def test_local_mode_uses_env_value_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("SERVICE_AUTH_SECRET", TEST_SERVICE_TOKEN)

    with patch("app.core.config.fetch_service_token") as fetch:
        settings = backend_config.get_settings()
    fetch.assert_not_called()
    assert settings.SERVICE_AUTH_SECRET == TEST_SERVICE_TOKEN


def test_non_local_mode_fetches_from_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("SERVICE_AUTH_SECRET", "")
    expected = "vault-issued-token-" + "x" * 40

    with patch("app.core.config.fetch_service_token", return_value=expected) as fetch:
        settings = backend_config.get_settings()
    fetch.assert_called_once()
    assert settings.SERVICE_AUTH_SECRET == expected


def test_non_local_mode_fails_loudly_when_vault_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("SERVICE_AUTH_SECRET", "ignored-because-vault-mode")
    monkeypatch.setenv("VAULT_ADDR", "http://127.0.0.1:1")  # unreachable on purpose

    with patch(
        "app.core.config.fetch_service_token",
        side_effect=VaultUnavailable("simulated outage"),
    ):
        with pytest.raises(VaultUnavailable):
            backend_config.get_settings()


def test_token_rotation_requires_cache_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rotation in Phase 1 happens via restart. Modeled here as a cache reset."""
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("SERVICE_AUTH_SECRET", "")

    with patch("app.core.config.fetch_service_token", return_value="token-A-" + "a" * 30):
        first = backend_config.get_settings()
    assert first.SERVICE_AUTH_SECRET.startswith("token-A-")

    # Without clearing the cache, the same Settings instance is returned even if
    # Vault now serves token-B.
    with patch("app.core.config.fetch_service_token", return_value="token-B-" + "b" * 30):
        still_first = backend_config.get_settings()
    assert still_first is first

    # After restart (modeled as cache.clear), the fresh fetch picks up token-B.
    backend_config.get_settings.cache_clear()
    with patch("app.core.config.fetch_service_token", return_value="token-B-" + "b" * 30):
        second = backend_config.get_settings()
    assert second.SERVICE_AUTH_SECRET.startswith("token-B-")
    assert second is not first


def test_vault_client_rejects_short_secret() -> None:
    """`fetch_service_token` must refuse a < 32-byte token, even if Vault returned it."""
    from app.core.vault import fetch_service_token

    with patch("app.core.vault.hvac.Client") as MockClient:
        MockClient.return_value = _hvac_stub_returning("too-short")
        with pytest.raises(VaultUnavailable, match="shorter than 32 bytes"):
            fetch_service_token(
                addr="http://vault:8200",
                token="dev-root-token",
                secret_path="concierge/service-auth",
            )


def test_vault_client_returns_token_on_success() -> None:
    from app.core.vault import fetch_service_token

    expected = "x" * 48
    with patch("app.core.vault.hvac.Client") as MockClient:
        MockClient.return_value = _hvac_stub_returning(expected)
        result = fetch_service_token(
            addr="http://vault:8200",
            token="dev-root-token",
            secret_path="concierge/service-auth",
        )
    assert result == expected


def test_vault_client_raises_on_auth_failure() -> None:
    from app.core.vault import fetch_service_token

    bad_client = MagicMock()
    bad_client.is_authenticated.return_value = False
    with patch("app.core.vault.hvac.Client", return_value=bad_client):
        with pytest.raises(VaultUnavailable, match="vault auth failed"):
            fetch_service_token(
                addr="http://vault:8200",
                token="bad",
                secret_path="concierge/service-auth",
            )
