-- Surgical schema repair: bring DB in line with the alembic stamp that was
-- applied without running 0006_tenant_config + 0008_agent_config.
--
-- The DB is already stamped at 0009_merge_heads (see alembic_version) but
-- the tenant_configs table was never created, so GET /tenant/config crashes
-- with UndefinedTableError. This script emits the exact DDL the two
-- migrations would have produced — nothing else.
--
-- Read-only-safe to re-run: every CREATE / ALTER uses IF NOT EXISTS / DO blocks.

BEGIN;

-- From 0006_tenant_config.upgrade()
CREATE TABLE IF NOT EXISTS tenant_configs (
    tenant_id          UUID PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    brand_name         VARCHAR(255),
    theme_color        VARCHAR(7),
    greeting           TEXT,
    public_description TEXT,
    contact_email      VARCHAR(255),
    allowed_origins    VARCHAR[] DEFAULT '{}',
    created_at         TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at         TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

ALTER TABLE tenant_configs ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policy
        WHERE polname = 'tenant_isolation'
          AND polrelid = 'public.tenant_configs'::regclass
    ) THEN
        CREATE POLICY tenant_isolation ON tenant_configs
            USING (tenant_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END$$;

-- From 0008_agent_config.upgrade()
ALTER TABLE tenant_configs ADD COLUMN IF NOT EXISTS persona        TEXT;
ALTER TABLE tenant_configs ADD COLUMN IF NOT EXISTS refusal_tone   VARCHAR(20);
ALTER TABLE tenant_configs ADD COLUMN IF NOT EXISTS enabled_tools  JSONB;
ALTER TABLE tenant_configs ADD COLUMN IF NOT EXISTS allowed_topics JSONB;
ALTER TABLE tenant_configs ADD COLUMN IF NOT EXISTS blocked_topics JSONB;

COMMIT;
