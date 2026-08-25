# Security Policy

## Supported versions

PRISMUX is pre-1.0 and developed on a single `main` branch. Security fixes are made against `main`; there is no separate maintenance branch to backport to yet.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for a security vulnerability.

Instead, use [GitHub's private vulnerability reporting](https://github.com/m3hrdadfi/prismux/security/advisories/new) for this repository.

You should get an initial response within a few days. If the report is confirmed, a fix will be prioritized and you'll be credited in the release notes unless you'd prefer otherwise.

## Scope

Areas most worth scrutiny, given what PRISMUX does:

- Authentication, session, and CSRF handling (`app/auth.py`, the security middleware in `app/main.py`)
- The outbound/SSRF policy that gates every provider request (`app/outbound.py`)
- Machine API key generation and verification (`app/auth.py`)
- Encrypted provider credential storage (`app/multi_provider.py`)

Reports about outdated third-party dependencies are welcome but lower priority — Dependabot already tracks those.
