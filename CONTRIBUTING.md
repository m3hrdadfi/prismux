# Contributing to PRISMUX

Thanks for considering a contribution. This is an early-stage, independently maintained project — issues, bug reports, and pull requests are all welcome.

## Before you start

For anything beyond a small fix, please open an issue first to discuss the change. It saves everyone time if a design direction gets agreed on before code is written, especially around provider adapters, routing behavior, and the outbound/SSRF policy, where correctness matters more than speed.

## Development setup

### Backend

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
cp .env.example .env
```

The backend expects PostgreSQL and Supabase Auth to be reachable — the easiest way to get both locally is the full Compose stack:

```sh
docker compose up --build
```

To run the backend outside Docker against that stack, point `DATABASE_URL` and `SUPABASE_AUTH_URL` at the exposed services and run:

```sh
uv run uvicorn app.main:app --reload --port 8100
```

Run the backend test suite with:

```sh
uv run pytest
```

### Frontend

Requires Node 22+.

```sh
cd frontend
npm install
npm run dev
```

Run the frontend test suite with:

```sh
npm run test
```

## Making changes

- Keep pull requests focused. A bug fix shouldn't carry an unrelated refactor.
- Match the existing style in the file you're editing rather than introducing a new pattern.
- Add or update tests for behavior you change, particularly around provider adapters, routing/fallback logic, pricing calculations, and auth/RBAC — these have the highest cost of a silent regression.
- If you touch the database schema, add an Alembic migration rather than a manual `ALTER`. Alembic is the only schema authority in this project; the application never runs DDL itself.
- If you touch outbound request handling (`app/outbound.py`) or anything in the request path to a provider, explain the security reasoning in the PR description — this is the SSRF boundary for the whole project.

## Commit messages

Write commit messages that explain *why*, not just *what* — the diff already shows what changed. Keep the subject line under ~70 characters.

## Submitting a pull request

1. Fork the repository and create a branch from `main`.
2. Make your change, with tests passing locally (`uv run pytest` and, if the frontend changed, `npm run test`).
3. Open a PR against `main` with a clear description of the change and, for anything user-facing, what you tested.
4. Be responsive to review feedback — this is a small project maintained part-time, so review may take a few days.

## Reporting bugs and security issues

- **Bugs**: open a GitHub issue with steps to reproduce, what you expected, and what happened.
- **Security issues**: please do not open a public issue. Instead, reach out privately so a fix can land before the report is public. See the repository's contact details for how to reach the maintainer.

## Code of conduct

Be respectful, assume good faith, and keep discussion focused on the work. Disagreement about technical approach is normal and welcome; personal attacks are not.
