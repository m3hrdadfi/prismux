"""Create the private PostgreSQL application schema.

Revision ID: 20260824_0001
Revises:
"""
from alembic import op

revision = "20260824_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS prismux")
    statements = """
        CREATE TABLE prismux.requests (
            id BIGSERIAL PRIMARY KEY,
            timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
            model TEXT,
            wait_ms INTEGER,
            status_code INTEGER,
            request_payload TEXT,
            response_payload TEXT,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            model_response_ms INTEGER,
            time_to_first_token_ms INTEGER,
            estimated_cost DOUBLE PRECISION,
            error_type TEXT,
            provider_id TEXT,
            upstream_model TEXT,
            route_alias TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 1 CHECK (attempt_count > 0)
        );
        CREATE INDEX idx_requests_timestamp ON prismux.requests(timestamp DESC);
        CREATE INDEX idx_requests_model ON prismux.requests(model);
        CREATE INDEX idx_requests_provider ON prismux.requests(provider_id, id DESC);

        CREATE TABLE prismux.request_stats (
            id BIGINT PRIMARY KEY,
            timestamp TIMESTAMPTZ NOT NULL,
            model TEXT,
            status_code INTEGER,
            wait_ms INTEGER,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            model_response_ms INTEGER,
            time_to_first_token_ms INTEGER,
            estimated_cost DOUBLE PRECISION,
            error_type TEXT,
            provider_id TEXT,
            upstream_model TEXT,
            route_alias TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 1 CHECK (attempt_count > 0)
        );
        CREATE INDEX idx_request_stats_timestamp ON prismux.request_stats(timestamp DESC);
        CREATE INDEX idx_request_stats_model ON prismux.request_stats(model);
        CREATE INDEX idx_request_stats_status ON prismux.request_stats(status_code);
        CREATE INDEX idx_request_stats_provider ON prismux.request_stats(provider_id, timestamp DESC);

        CREATE TABLE prismux.queue_samples (
            id BIGSERIAL PRIMARY KEY,
            timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
            queue_depth INTEGER NOT NULL CHECK (queue_depth >= 0),
            token_level DOUBLE PRECISION NOT NULL CHECK (token_level >= 0),
            provider_id TEXT
        );
        CREATE INDEX idx_queue_samples_timestamp ON prismux.queue_samples(timestamp DESC);
        CREATE INDEX idx_queue_samples_provider ON prismux.queue_samples(provider_id, timestamp DESC);

        CREATE TABLE prismux.app_settings (
            key TEXT PRIMARY KEY,
            value JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE prismux.providers (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            config_json JSONB NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT true,
            is_default BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX uq_providers_one_default ON prismux.providers ((is_default)) WHERE is_default;

        CREATE TABLE prismux.provider_credentials (
            provider_id TEXT PRIMARY KEY REFERENCES prismux.providers(id) ON DELETE CASCADE,
            encrypted_value TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE TABLE prismux.provider_models (
            provider_id TEXT NOT NULL REFERENCES prismux.providers(id) ON DELETE CASCADE,
            model_id TEXT NOT NULL,
            discovered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (provider_id, model_id)
        );
        CREATE INDEX idx_provider_models_sort ON prismux.provider_models(provider_id, lower(model_id));

        CREATE TABLE prismux.model_routes (
            alias TEXT PRIMARY KEY CHECK (position('::' in alias) = 0),
            enabled BOOLEAN NOT NULL DEFAULT true,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE TABLE prismux.route_targets (
            alias TEXT NOT NULL REFERENCES prismux.model_routes(alias) ON DELETE CASCADE,
            priority INTEGER NOT NULL CHECK (priority >= 0),
            provider_id TEXT NOT NULL REFERENCES prismux.providers(id),
            model_id TEXT NOT NULL,
            PRIMARY KEY (alias, priority)
        );
        CREATE TABLE prismux.model_pricing (
            provider_id TEXT NOT NULL REFERENCES prismux.providers(id) ON DELETE CASCADE,
            model_id TEXT NOT NULL,
            input_per_1k NUMERIC(20, 10) NOT NULL DEFAULT 0 CHECK (input_per_1k >= 0),
            output_per_1k NUMERIC(20, 10) NOT NULL DEFAULT 0 CHECK (output_per_1k >= 0),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (provider_id, model_id)
        );
        CREATE TABLE prismux.request_attempts (
            id BIGSERIAL PRIMARY KEY,
            request_id BIGINT REFERENCES prismux.requests(id) ON DELETE CASCADE,
            attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
            provider_id TEXT NOT NULL,
            upstream_model TEXT NOT NULL,
            wait_ms INTEGER NOT NULL DEFAULT 0 CHECK (wait_ms >= 0),
            response_ms INTEGER,
            status_code INTEGER,
            error_type TEXT,
            fallback_reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (request_id, attempt_number)
        );

        CREATE TABLE prismux.user_roles (
            user_id UUID PRIMARY KEY,
            email TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('admin', 'operator', 'viewer')),
            disabled BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX uq_user_roles_email ON prismux.user_roles(lower(email));

        CREATE TABLE prismux.proxy_api_keys (
            id UUID PRIMARY KEY,
            name TEXT NOT NULL,
            key_prefix TEXT NOT NULL UNIQUE,
            secret_digest BYTEA NOT NULL,
            scopes TEXT[] NOT NULL DEFAULT ARRAY['prismux:invoke']::TEXT[],
            created_by UUID NOT NULL REFERENCES prismux.user_roles(user_id) ON DELETE RESTRICT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ,
            revoked_at TIMESTAMPTZ,
            last_used_at TIMESTAMPTZ,
            CHECK (expires_at IS NULL OR expires_at > created_at)
        );

        CREATE TABLE prismux.audit_events (
            id BIGSERIAL PRIMARY KEY,
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            actor_type TEXT NOT NULL CHECK (actor_type IN ('user', 'api_key', 'system', 'anonymous')),
            actor_id TEXT,
            action TEXT NOT NULL,
            target_type TEXT,
            target_id TEXT,
            outcome TEXT NOT NULL CHECK (outcome IN ('success', 'denied', 'failure')),
            source_ip INET,
            user_agent TEXT,
            details JSONB NOT NULL DEFAULT '{}'::JSONB
        );
        CREATE INDEX idx_audit_events_time ON prismux.audit_events(occurred_at DESC);
        CREATE INDEX idx_audit_events_actor ON prismux.audit_events(actor_type, actor_id, occurred_at DESC);

        REVOKE ALL ON SCHEMA prismux FROM PUBLIC;
        GRANT USAGE ON SCHEMA prismux TO prismux_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA prismux TO prismux_app;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA prismux TO prismux_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA prismux GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO prismux_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA prismux GRANT USAGE, SELECT ON SEQUENCES TO prismux_app;
    """
    for statement in statements.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    for table in (
        "audit_events", "proxy_api_keys", "user_roles", "request_attempts", "model_pricing",
        "route_targets", "model_routes", "provider_models", "provider_credentials", "providers",
        "app_settings", "queue_samples", "request_stats", "requests",
    ):
        op.execute(f'DROP TABLE IF EXISTS prismux."{table}" CASCADE')
