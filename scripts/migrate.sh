#!/bin/sh
set -eu

: "${DATABASE_MIGRATION_URL:?DATABASE_MIGRATION_URL is required}"
: "${PROXY_DATABASE_PASSWORD:?PROXY_DATABASE_PASSWORD is required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"

MIGRATION_PSQL_URL=$(printf '%s' "$DATABASE_MIGRATION_URL" | sed 's#postgresql+asyncpg://#postgresql://#')

psql "$MIGRATION_PSQL_URL" \
  -v ON_ERROR_STOP=1 \
  -v proxy_password="$PROXY_DATABASE_PASSWORD" \
  -v postgres_password="$POSTGRES_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE prismux_app LOGIN PASSWORD %L', :'proxy_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'prismux_app') \gexec
SELECT format('ALTER ROLE prismux_app PASSWORD %L', :'proxy_password') \gexec
ALTER ROLE prismux_app SET search_path = prismux, public;

SELECT 'CREATE ROLE anon NOLOGIN'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') \gexec
SELECT 'CREATE ROLE authenticated NOLOGIN'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') \gexec
SELECT 'CREATE ROLE service_role NOLOGIN BYPASSRLS'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') \gexec
SELECT format('CREATE ROLE authenticator LOGIN NOINHERIT PASSWORD %L', :'postgres_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticator') \gexec
SELECT format('ALTER ROLE authenticator PASSWORD %L', :'postgres_password') \gexec
GRANT anon, authenticated, service_role TO authenticator;

SELECT format('CREATE ROLE supabase_storage_admin LOGIN PASSWORD %L', :'postgres_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'supabase_storage_admin') \gexec
SELECT format('ALTER ROLE supabase_storage_admin PASSWORD %L', :'postgres_password') \gexec
ALTER ROLE supabase_storage_admin SET search_path = storage, public;
GRANT CONNECT, CREATE ON DATABASE postgres TO supabase_storage_admin;
GRANT anon, authenticated, service_role TO supabase_storage_admin;

CREATE SCHEMA IF NOT EXISTS storage AUTHORIZATION supabase_storage_admin;
ALTER SCHEMA storage OWNER TO supabase_storage_admin;
GRANT ALL ON SCHEMA storage TO supabase_storage_admin WITH GRANT OPTION;
GRANT USAGE ON SCHEMA storage TO postgres, anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE supabase_storage_admin IN SCHEMA storage
  GRANT ALL ON TABLES TO postgres, anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE supabase_storage_admin IN SCHEMA storage
  GRANT ALL ON FUNCTIONS TO postgres, anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE supabase_storage_admin IN SCHEMA storage
  GRANT ALL ON SEQUENCES TO postgres, anon, authenticated, service_role;
SQL

alembic upgrade head
