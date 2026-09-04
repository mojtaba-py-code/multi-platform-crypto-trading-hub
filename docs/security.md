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
- **Refresh-token reuse detection**: rotation alone does not help the victim of
  a theft — whoever presents the token first wins the race and leaves with a
  valid new pair, while the other party sees one failed refresh and simply logs
  in again. So a *second* use of an already-rotated token is treated as proof
  the token leaked, and **every outstanding session for that user is dropped**,
  including the pair the thief just minted. This is a per-user generation
  counter (`users.token_generation`) carried in each token rather than a
  timestamp: `iat` has one-second resolution, so a timestamp watermark would
  either miss a token minted in the same second or permanently reject the next
  login's. Access tokens already issued are deliberately not re-checked — they
  are short-lived, and enforcing this per request would put a database read in
  front of every endpoint.
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

- Security-relevant events (register, login, login-failed, login-to-a-disabled
  account, lockout, 2fa events, refresh-token reuse, account create/delete,
  order place/cancel) are written to an append-only `audit_logs` table with only
  non-sensitive context.
- Events that accompany a **rejected** request are committed independently of
  the request transaction. The request-scoped unit of work rolls back whenever a
  handler raises, which is correct for business writes and exactly wrong here:
  every event worth investigating — a failed login, a lockout, a reused refresh
  token — is recorded on a path that ends in an exception, so without this the
  log would contain successes only.
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
- Behind a reverse proxy, set **`TRUSTED_PROXY_IPS`** to the proxy's address or
  range. The rate limiter and the audit trail then read the real client from
  `X-Forwarded-For`; left unset, both see the proxy, so the whole deployment
  shares one rate-limit bucket and every audit entry records the proxy. The
  header is honoured *only* when the peer that opened the connection is listed,
  and the chain is read from the right — the end the proxy appends to — so a
  caller cannot forge an address by supplying their own header.
- A disabled user keeps *access* (not refresh) until the current short-lived
  access token expires, since access tokens are not checked against a store on
  every request.
- The paper-trading liquidation/position model is simplified and not
  exchange-exact.
