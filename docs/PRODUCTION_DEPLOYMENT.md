# PRISMUX PostgreSQL and Supabase Auth deployment

The complete runtime is managed by the repository's main Compose file. It starts private PostgreSQL and Supabase Auth services, runs the Alembic migration container, and starts FastAPI only after those dependencies are healthy.

## Start

Copy `.env.example` to `.env` and replace every `replace-this-...` value. Generate independent PostgreSQL, application-role, JWT, credential-encryption, API-key-pepper, and bootstrap-admin secrets. For local HTTP, keep `COOKIE_SECURE=false`; set it to `true` behind production TLS.

Start everything with one command:

```sh
docker compose up --build
```

PostgreSQL, Auth, and PostgreSQL metadata are not published to the host. PRISMUX is published on `${PROXY_PORT:-8100}` and Supabase Studio is bound to localhost only on `${SUPABASE_STUDIO_PORT:-8083}`. The migration service creates the non-superuser `proxy_app` role and applies Alembic before FastAPI starts.

Open Supabase Studio at `http://localhost:8083` and sign in with `DASHBOARD_USERNAME` and `DASHBOARD_PASSWORD`. The gateway stores only a bcrypt password hash in its runtime filesystem. Studio uses the existing PostgreSQL database, Auth, PostgREST, and Storage services; its legacy anon and service-role API keys are generated inside a private Docker volume from `SUPABASE_JWT_SECRET` and are never written to `.env`. If Studio is needed remotely, keep the localhost bind and use an SSH tunnel or an authenticated TLS reverse proxy rather than publishing it directly.

Supabase Storage keeps uploaded objects in the `supabase_storage_data` Docker named volume and metadata in PostgreSQL. The named volume is intentional: Docker Desktop bind mounts on macOS can lack filesystem features required by Storage. Back up both PostgreSQL and this volume, or configure the Storage service to use an off-host S3-compatible backend before relying on it for durable production files.

The bootstrap Auth user is created only when its email does not already exist. On its first successful login, the application atomically assigns it the Admin role. Remove `BOOTSTRAP_ADMIN_PASSWORD` from the deployment environment after that first login.

## Production settings

- Use a TLS reverse proxy and set `SUPABASE_PUBLIC_URL`, `COOKIE_SECURE=true`, and exact `TRUSTED_PROXY_CIDRS`.
- Public signup remains disabled. Admins create users from Access settings.
- Keep Supabase Studio restricted to localhost or an administrator-only tunnel. It has database administration capabilities.
- Do not expose PostgreSQL or Supabase Auth ports.
- Provider destinations are permitted by default. Restrict deployments with `OUTBOUND_DISALLOWED_HOSTS`, `OUTBOUND_DISALLOWED_CIDRS`, and `OUTBOUND_DISALLOWED_PORTS`; metadata destinations remain permanently denied.
- Rotate provider credentials, machine keys, JWT secrets, database passwords, and the settings-encryption key independently.
- Alembic is the only application schema authority; FastAPI cannot perform DDL.

Persistent application and Auth data live in the Compose-managed `postgres_data` volume. Removing that volume permanently removes the database.

## OpenAI-compatible client endpoints

Create a machine key under **Access → Machine API keys** and use it as the client's API key. Provider credentials stay inside the proxy and are never given to clients.

- `http://localhost:8100/v1` uses route aliases, qualified `provider_id::model_id` selectors, and the default provider for raw model IDs.
- `http://localhost:8100/{provider_id}/v1` sends every request directly to that enabled provider. The path uses the stable provider ID shown in Settings, not its display name.
- `GET /v1/models` returns route aliases and provider-qualified cached models.
- `GET /{provider_id}/v1/models` returns that provider's cached model IDs without qualification.

For example, an OpenAI-compatible client can bypass routing and use NVIDIA directly:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8100/nvidia/v1",
    api_key="prismux_live_...",
)

response = client.chat.completions.create(
    model="meta/muse-glimmer-30b",
    messages=[{"role": "user", "content": "Hello"}],
)
```

Provider-scoped endpoints reject a model qualified for a different provider. Streaming uses the same base URL and `stream=True`.

Machine keys issued before the PRISMUX rename with the legacy `rlp_live_` prefix remain valid until revoked.

References: [Supabase self-hosting](https://supabase.com/docs/guides/self-hosting), [Supabase Auth configuration](https://supabase.com/docs/guides/self-hosting/auth/config).
