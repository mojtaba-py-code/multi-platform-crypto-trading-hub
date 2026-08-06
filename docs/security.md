# Security model

Security is a primary design goal, not an afterthought. This document describes
the controls and the threat model they address.

## Secrets at rest

- Exchange API keys, secrets and passphrases are **encrypted before storage**
  using `SecretCipher` (Fernet: AES-128-CBC + HMAC-SHA256) with a master key
  held only in the environment / a secret manager.
- **Key rotation** is built in via `MultiFernet`. `MASTER_ENCRYPTION_KEY` accepts
  a comma-separated list: the **first** key encrypts, every key can decrypt. To
  rotate, prepend a new key, let stored secrets be re-encrypted, then drop the
  old one.
- The TOTP 2FA secret is treated as a secret and encrypted the same way.
- Plaintext secrets exist only transiently in memory while a request is served.
  They are **never** logged or serialised (`ExchangeCredentials.__repr__` and the
  account schemas omit them; the log processor redacts them).

## Authentication & authorization

- **Passwords**: Argon2id hashing with transparent rehash-on-verify when
  parameters are upgraded. Plaintext passwords are never stored. Hashing is
  deliberately expensive (~64 MiB, tens of ms), so it runs on a worker thread —
  running it inline would let a burst of logins stall the event loop and take
  the whole process down with it.
- **Tokens**: JWT access (short-lived) + refresh (long-lived) with a `type`
  claim and unique `jti`; type confusion is rejected on decode. **Refresh tokens
  are rotated** — each refresh invalidates the presented token (one-time use)
  via a revocation store, and **logout** revokes the refresh token so a stolen
  copy cannot renew the session. The store is in-process by default and
  Redis-backed (shared across replicas) when `USE_REDIS_AUTH_STORE=true`.
- **Brute-force protection**: a per-account throttle locks an identity after
  `LOGIN_MAX_ATTEMPTS` failed password/2FA attempts for `LOGIN_LOCKOUT_SECONDS`
  (returns `429` with `Retry-After`), defending against distributed guessing
  that the per-IP limiter would miss. Like the refresh denylist it is in-process
  by default and Redis-backed when `USE_REDIS_AUTH_STORE=true`; the Redis keys
  are hashed so the keyspace is not a user list.
- **2FA**: TOTP (RFC 6238) with a small drift window. Codes are **single-use**:
  the last accepted time step is recorded per user and anything at or below it
  is refused, so an observed code cannot be replayed within its window.
  Enrolment requires proving possession before 2FA is enabled, re-enrolment
  while 2FA is live is refused (otherwise a stolen access token alone could swap
  the second factor), and disabling requires a valid current code.
- **RBAC**: explicit permission model (`security/rbac.py`). Endpoints check
  fine-grained permissions (e.g. `order:create`), not raw roles. Every route
  that reaches an exchange — including market data and indicators — requires
  `market:read`, so the service cannot be used as an anonymous exchange proxy
  against its own IP reputation. The public surface is only `/health`, `/ready`,
  the static `/exchanges` registry, and the dashboard page itself.

## Trading safety (defence against costly mistakes)

- `ALLOW_LIVE_TRADING=false` by default: **all** orders are simulated. This is
  enforced in one place (`ExchangeFactory`) that clients cannot override.
- Live adapters are **testnet-first** (`USE_EXCHANGE_TESTNET=true`).
- Pre-trade **risk limits** (max leverage, position share of equity) reject
  dangerous orders before they are sent.
- Simulated accounts **reserve** the funds a resting order would need, so paper
  results cannot be flattered by orders the balance could never have covered.

## Transport & web hardening

- Security headers on every response: `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, `Content-Security-Policy`, `Strict-Transport-Security`.
- The API's CSP is `default-src 'self'`. The bundled dashboard is a single file
  with inline CSS/JS, which that policy would block — so instead of weakening it
  with `'unsafe-inline'`, the `/dashboard` route mints a **per-response nonce**
  and serves a stricter `default-src 'none'` policy scoped to it. The page
  carries no inline `onclick=` handlers, since nonces cannot whitelist those.
- **CORS** restricted to a configured allow-list.
- **Rate limiting** per client IP (app middleware + optional Nginx edge limit).
- TLS terminated at Nginx in production (see `deploy/nginx.conf`).

## Injection & validation

- **SQL injection**: all access goes through SQLAlchemy's parameterised queries;
  no string-built SQL.
- **Input validation**: Pydantic v2 schemas validate and coerce every request;
  unknown exchanges, malformed orders, and weak passwords are rejected at the
  boundary. Trading symbols and account labels are constrained to a strict
  charset (no HTML/script characters) because they are stored and later
  rendered; candle timeframes are checked against an allow-list before any
  exchange call.
- **Output encoding (XSS)**: the web dashboard escapes every server-derived
  value before inserting it into the DOM.
- **User enumeration**: login runs a full Argon2 verification even for unknown
  emails, so response timing does not reveal whether an account exists.
- **Error handling**: internal exceptions are mapped to sanitised JSON; stack
  traces and internal messages are never returned to clients.

## Auditing & logging

- Security-relevant events (register, login, login-failed, 2fa events, account
  create/delete, order place/cancel) are written to an append-only `audit_logs`
  table with only non-sensitive context.
- Structured logs (`structlog`) run every event through a **redaction processor**
  that masks known-sensitive keys, so secrets cannot leak even by accident.

## Operational guidance

- Provide `MASTER_ENCRYPTION_KEY` and `JWT_SECRET_KEY` via a secret manager, not
  files in the image. The app **refuses to start in production** without them.
- Give exchange API keys the **least privilege** needed (disable withdrawals;
  IP-allowlist where the exchange supports it).
- Rotate the master key periodically; re-encrypt stored secrets afterwards.
- Keep `APP_DEBUG=false` in production (enforced).

## Known limitations (v1.0)

- The in-process **request** rate limiter is per-replica (idle buckets are swept
  so they cannot grow unbounded); a Redis-backed limiter is recommended for the
  per-IP limit at scale. The auth throttle and refresh denylist already have
  Redis backends — set `USE_REDIS_AUTH_STORE=true` in any multi-replica
  deployment, or each replica grants its own quota of password guesses.
- The rate limiter keys on the socket peer address, so behind a proxy it must be
  fronted by one that enforces its own per-client limit (see `deploy/nginx.conf`).
- A disabled user keeps *access* (not refresh) until the current short-lived
  access token expires, since access tokens are not checked against a store on
  every request.
- The paper-trading liquidation/position model is simplified and not
  exchange-exact.
