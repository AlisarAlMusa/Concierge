"""HashiCorp Vault client — single entry point for the shared service credential.

Phase 1 (spec 018): only `fetch_service_token()` is exposed. See the backend's
equivalent at `backend/app/core/vault.py` for the architectural rationale.

Never log the token value. Log path + addr only.
"""

from __future__ import annotations

import logging

import hvac

logger = logging.getLogger(__name__)

_MIN_TOKEN_BYTES = 32


class VaultUnavailable(RuntimeError):
    """Raised when Vault is unreachable, unauthenticated, or returns a missing/short secret."""


def fetch_service_token(
    addr: str,
    token: str,
    secret_path: str,
    *,
    mount_point: str = "kv",
    timeout: float = 2.0,
) -> str:
    try:
        client = hvac.Client(url=addr, token=token, timeout=timeout)
        if not client.is_authenticated():
            raise VaultUnavailable(f"vault auth failed at addr={addr}")
        response = client.secrets.kv.v2.read_secret_version(
            path=secret_path,
            mount_point=mount_point,
            raise_on_deleted_version=True,
        )
    except VaultUnavailable:
        raise
    except Exception as exc:
        raise VaultUnavailable(
            f"vault read failed at addr={addr} path={mount_point}/{secret_path}"
        ) from exc

    data = response.get("data", {}).get("data", {})
    secret = data.get("token")
    if not isinstance(secret, str) or len(secret) < _MIN_TOKEN_BYTES:
        raise VaultUnavailable(
            f"service-auth secret missing or shorter than {_MIN_TOKEN_BYTES} bytes "
            f"at {mount_point}/{secret_path}"
        )
    logger.info("Fetched service-auth secret from Vault (addr=%s path=%s)", addr, secret_path)
    return secret
