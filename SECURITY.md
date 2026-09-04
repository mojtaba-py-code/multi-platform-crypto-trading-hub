# Security Policy

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Report it privately through GitHub:
[**Report a vulnerability**](https://github.com/mojtaba-py-code/multi-platform-crypto-trading-hub/security/advisories/new)
(Security → Advisories → Report a vulnerability). The report stays private
between you and the maintainer until a fix is published.

Please include the affected version or commit, what an attacker gains, and the
smallest reproduction you have. You will get an acknowledgement within 7 days.
Fixes are released before any public disclosure, and you will be credited in the
advisory unless you would rather not be.

## Supported versions

| Version | Supported |
| --- | --- |
| 1.x | ✅ |

## Scope

This project is a self-hosted application: there is no service operated by the
maintainer to attack. Reports should concern the code in this repository.

In scope, and worth reporting:

- Authentication or authorisation bypass — reading or acting on another user's
  accounts, orders, or exchange credentials.
- Any path that exposes a stored exchange API key, secret, passphrase, or TOTP
  secret in plaintext, including via logs, error responses, or the OpenAPI schema.
- Bypassing the paper-trading guarantee — causing a real order to be routed to
  an exchange while `ALLOW_LIVE_TRADING=false`.
- Weaknesses in credential encryption, password hashing, JWT handling, TOTP
  replay protection, or the brute-force lockout.
- Injection, SSRF, or remote code execution.

Out of scope:

- Missing hardening in `docker-compose.yml` beyond what is documented — it is a
  development stack, not a production deployment. See
  [`docs/deployment.md`](docs/deployment.md).
- Findings that require an attacker who already has the server's
  `MASTER_ENCRYPTION_KEY`, database, or shell.
- Losses from your own trading decisions, or from exchange API keys you chose to
  grant withdrawal permission to.

## How this project defends itself

Detail lives in [`docs/security.md`](docs/security.md); the controls in short:

| Concern | Control |
| --- | --- |
| Exchange credentials at rest | Fernet (AES-128-CBC + HMAC-SHA256) with a rotatable master key; plaintext never reaches the database |
| Passwords | Argon2id (64 MiB, t=3), verified off the event loop, transparent rehash on parameter upgrade |
| Sessions | Short-lived JWT access tokens; refresh tokens are single-use, rotated, and revocable |
| Two-factor | TOTP with the accepted time step recorded, so a code cannot be replayed |
| Brute force | Per-account lockout (Redis-backed across replicas) plus a per-IP rate limit |
| Accidental disclosure | Structured logging redacts known-sensitive keys; credential objects have a scrubbing `__repr__`; 500s never return internals |
| Unsafe deployment | Production refuses to start without a master key, a ≥32-char JWT secret, `APP_DEBUG=false`, and a non-wildcard CORS allow-list |
| Live trading | Off by default and enforced at one choke point (`ExchangeFactory`); testnet-first when enabled |

## Automated checks

Every push and pull request runs, and must pass:

- **gitleaks** over the full commit history and the working tree — a secret that
  was committed and later removed is still in the pack files, so scanning only
  the tip would miss the case that matters. Findings are redacted in the log.
- **pip-audit** (`--strict`) against the Python Packaging Advisory Database.
- **CodeQL** (`security-and-quality`) for inter-procedural taint analysis.
- **ruff** with the `S` (Bandit) ruleset, **mypy**, and the test suite with a
  90% coverage floor.

Dependabot proposes weekly updates for pip, GitHub Actions, and Docker.

## Operating this safely

If you connect real exchange API keys:

1. Create keys **scoped to trading only** — never enable withdrawal.
2. IP-allowlist the keys at the exchange to your server's address.
3. Keep `USE_EXCHANGE_TESTNET=true` until you have verified behaviour end to end.
4. Set a real `MASTER_ENCRYPTION_KEY` and back it up separately from the
   database. Losing it means losing every stored credential; leaking it while
   the database also leaks means losing the credentials to an attacker.
5. Serve only over TLS, and set `CORS_ORIGINS` to your own front end.
