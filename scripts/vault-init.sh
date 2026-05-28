#!/bin/sh
set -e

echo "Seeding Vault at ${VAULT_ADDR}..."

# Write all required secrets in one operation
vault kv put secret/concierge \
  jwt_secret="${JWT_SECRET}" \
  service_auth_secret="${SERVICE_AUTH_SECRET}" \
  widget_token_secret="${WIDGET_TOKEN_SECRET}" \
  minio_secret_key="${MINIO_SECRET_KEY}" \
  postgres_password="${POSTGRES_PASSWORD}"

# Patch in optional LLM API keys only when provided
if [ -n "${OPENAI_API_KEY}" ]; then
  vault kv patch secret/concierge openai_api_key="${OPENAI_API_KEY}"
  echo "  openai_api_key seeded."
fi

if [ -n "${ANTHROPIC_API_KEY}" ]; then
  vault kv patch secret/concierge anthropic_api_key="${ANTHROPIC_API_KEY}"
  echo "  anthropic_api_key seeded."
fi

if [ -n "${AZURE_OPENAI_API_KEY}" ]; then
  vault kv patch secret/concierge azure_openai_api_key="${AZURE_OPENAI_API_KEY}"
  echo "  azure_openai_api_key seeded."
fi

echo "Vault seeded successfully."
