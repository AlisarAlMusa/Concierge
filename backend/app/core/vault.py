import logging

import httpx

logger = logging.getLogger(__name__)

_VAULT_PATH = "/v1/secret/data/concierge"


async def fetch_vault_secrets(vault_addr: str, vault_token: str) -> dict[str, str]:
    """Fetch all secrets from Vault KV v2 at secret/concierge.

    Returns empty dict and logs a warning on any failure so the app can
    fall back to .env values rather than refusing to start.
    """
    url = f"{vault_addr}{_VAULT_PATH}"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, headers={"X-Vault-Token": vault_token})
            resp.raise_for_status()
            return resp.json()["data"]["data"]
    except Exception as exc:
        logger.warning("vault_fetch_failed addr=%s error=%s — falling back to env vars", vault_addr, exc)
        return {}
