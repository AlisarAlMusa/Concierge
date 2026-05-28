#!/bin/sh
# seed_vault.sh — idempotently provision the shared service-auth secret.
#
# Run by the `vault-init` Compose service after `vault` is healthy. Safe to
# re-run across `docker compose up` cycles — if the secret already exists at
# the target path, the existing value is preserved.
#
# Expected env: VAULT_ADDR, VAULT_TOKEN.
set -eu

: "${VAULT_ADDR:?VAULT_ADDR must be set}"
: "${VAULT_TOKEN:?VAULT_TOKEN must be set}"
SECRET_PATH="${VAULT_SERVICE_AUTH_PATH:-concierge/service-auth}"

# Enable KV v2 at `kv/` if not already enabled. `vault secrets enable` errors
# when the mount already exists, so swallow that specific failure.
if ! vault secrets list -format=json | grep -q '"kv/"'; then
    vault secrets enable -path=kv -version=2 kv
fi

# Only write the secret if it does not exist. `vault kv get` returns non-zero
# when the secret is missing; we use that to drive the "create-if-absent" flow.
if vault kv get -mount=kv "${SECRET_PATH}" >/dev/null 2>&1; then
    echo "service-auth secret already present at kv/${SECRET_PATH} — not overwriting"
else
    # 48 base64 chars ≈ 36 random bytes. Easily passes the 32-byte minimum
    # enforced in core/vault.py.
    TOKEN_VALUE="$(head -c 36 /dev/urandom | base64 | tr -d '\n')"
    vault kv put -mount=kv "${SECRET_PATH}" token="${TOKEN_VALUE}"
    echo "Wrote new service-auth secret to kv/${SECRET_PATH}"
fi
