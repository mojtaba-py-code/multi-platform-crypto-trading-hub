# Multi-Platform Cryptocurrency Trading Hub

A unified, security-first platform to manage cryptocurrency trading across
multiple exchanges from a single API and dashboard. Built with FastAPI, async
SQLAlchemy, PostgreSQL, Redis and Celery, following clean architecture and
SOLID principles.

> **Safety first.** Trading is **simulated (paper trading) by default**. Live
> order routing is impossible unless an operator explicitly enables it *and*
> connects a live account — and even then it is **testnet-first**. This is
> enforced at a single choke point (`ExchangeFactory`), not sprinkled across the
> code, so it cannot be bypassed by a client request.

---

## Highlights

- **9 exchanges out of the box** via a clean Adapter over [ccxt](https://github.com/ccxt/ccxt):
  Binance, Bybit, OKX, KuCoin, Kraken, Coinbase, Bitget, Gate.io, MEXC.
  Adding another is a *one-line* registry entry.
- **Encrypted credentials at rest** — API keys/secrets/passphrases are encrypted
  with Fernet (AES-128-CBC + HMAC) using a rotatable master key
  (`MASTER_ENCRYPTION_KEY` takes a comma-separated list: the first key encrypts,
  all of them decrypt). Plaintext never hits the database or the logs.
- **Real authentication** — Argon2id password hashing (off the event loop), JWT
  access/refresh tokens with rotation and revocation, single-use TOTP two-factor
  codes, per-account brute-force lockout, and role-based access control
  (viewer / trader / admin).
- **Paper trading engine** that fills against **live public market data**, so you
  can practise with real prices at zero financial risk. Simulated balances and
  orders are **persisted per account in the database**, so they survive restarts;
  resting orders reserve the funds they would need, and buys, sells and
  cancellation all behave correctly.
- **Technical analysis** — RSI, SMA, EMA, MACD, Bollinger Bands, ATR, ADX,
  Stochastic, SuperTrend, VWAP, pivot points, trend detection (dependency-free,
  fully unit-tested). All are reachable via the `/analytics/.../indicator`
  endpoint (multi-line indicators return their primary series).
- **Risk tooling** — position sizing, R:R, PnL, liquidation price, and portfolio
  guard rails checked before every order.
- **Production plumbing** — structured logging with secret redaction, circuit
  breaker + retry for exchange calls, rate limiting, security headers, Docker /
  Compose / Nginx, Celery workers + beat, Alembic migrations (with a CI check
  that the schema and the models cannot drift apart), and 95% test coverage.

---

## Architecture

```
             ┌──────────────┐      ┌──────────────────────────────┐
 HTTP  ───▶  │  FastAPI     │────▶ │  Services (business logic)   │
 client      │  routers     │      │  auth · account · market ·   │
             │  + DI        │      │  trading · portfolio         │
             └──────────────┘      └───────┬───────────┬──────────┘
                    │                       │           │
                    ▼                       ▼           ▼
             ┌──────────────┐      ┌────────────┐  ┌──────────────────┐
             │ Repositories │      │  Security  │  │ ExchangeFactory  │
             │ (SQLAlchemy) │      │ crypto·jwt │  │  (safety policy) │
             └──────┬───────┘      │ totp·rbac  │  └───────┬──────────┘
                    ▼              └────────────┘          ▼
             ┌──────────────┐                     ┌──────────────────┐
             │ PostgreSQL   │                     │ Adapter (port)   │
             └──────────────┘                     │  ├ CcxtAdapter   │
                                                  │  └ PaperAdapter  │
             ┌──────────────┐   ┌──────────────┐  └───────┬──────────┘
             │ Redis (cache │   │ Celery beat/ │          ▼
             │  + broker)   │◀──│  workers     │     9 exchanges
             └──────────────┘   └──────────────┘
```

The layering is strict: **routers → services → repositories → models**. Services
depend on abstractions (`ExchangeAdapter`, repository interfaces), never on
concrete exchanges or SQL. See [`docs/architecture.md`](docs/architecture.md).

---

## Quick start (local, no database required)

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   |   Unix: source .venv/bin/activate
pip install -e ".[dev]"

# Generate secrets and create your .env
cp .env.example .env
python -m app.scripts.gen_keys          # paste output into .env

# Run against a throwaway SQLite DB (dev only)
DATABASE_URL="sqlite+aiosqlite:///./dev.db" uvicorn app.main:app --reload
```

Then open:

- **Dashboard:** http://localhost:8000/dashboard
- **Swagger UI:** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc

## Quick start (full stack with Docker)

```bash
cp .env.example .env && python -m app.scripts.gen_keys   # fill in .env
docker compose up --build
```

This starts PostgreSQL, Redis, the API, a Celery worker, the scheduler, and an
Nginx reverse proxy on port 80.

---

## Example: from zero to a (paper) trade

```bash
BASE=http://localhost:8000/api/v1
curl -X POST $BASE/auth/register -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"a-strong-password"}'

TOKEN=$(curl -s -X POST $BASE/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"a-strong-password"}' | jq -r .access_token)

ACC=$(curl -s -X POST $BASE/accounts/paper -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"exchange_id":"binance","label":"main","market_type":"spot"}' | jq -r .id)

# Fills at the real, live Binance price — but it is simulated.
curl -X POST $BASE/trading/orders -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"account_id\":\"$ACC\",\"symbol\":\"BTC/USDT\",\"side\":\"buy\",\"type\":\"market\",\"amount\":\"0.01\"}"
```

---

## Configuration

All configuration is environment-driven and validated at startup
(`app/config/settings.py`). Key switches:

| Variable | Default | Meaning |
| --- | --- | --- |
| `ALLOW_LIVE_TRADING` | `false` | Master switch. When false, **all** orders are simulated. |
| `USE_EXCHANGE_TESTNET` | `true` | Live adapters connect to exchange sandbox/testnet. |
| `MASTER_ENCRYPTION_KEY` | — | Fernet key(s) for encrypting stored API secrets — comma-separated to rotate (required in prod). |
| `JWT_SECRET_KEY` | — | HS256 signing secret (required in prod, ≥ 32 chars). |
| `USE_REDIS_AUTH_STORE` | `false` | Share the refresh denylist and login lockout across replicas. Set `true` for multi-replica. |
| `DATABASE_URL` | postgres… | Async SQLAlchemy DSN. |
| `RATE_LIMIT_PER_MINUTE` | `120` | Per-IP request budget. |

In `production`, the app **refuses to start** without a master key, a strong JWT
secret, `APP_DEBUG=false`, and a CORS allow-list that is not `*`.

---

## Development

```bash
pytest --cov=app --cov-report=term-missing   # tests + coverage
ruff check alembic app tests                 # lint
ruff format alembic app tests                # format
mypy app                                     # type check
```

Database migrations. The v1.0 schema is committed, so a production deploy is
just:

```bash
alembic upgrade head
```

After changing a model, generate the matching revision (CI fails if you forget —
it runs `alembic check`):

```bash
alembic revision --autogenerate -m "describe the change"
```

Outside production the app creates tables on startup, so local development needs
no migration step.

Background jobs:

```bash
celery -A app.workers.celery_app.celery worker --loglevel=info
celery -A app.workers.celery_app.celery beat   --loglevel=info
```

---

## Documentation

- [Architecture](docs/architecture.md)
- [Security model](docs/security.md)
- [Deployment guide](docs/deployment.md)

## Project layout

```
app/
  api/          FastAPI routers, DI, middleware, error handlers
  analytics/    technical indicators (pure functions)
  config/       validated settings
  core/         exceptions, logging, resilience (circuit breaker/retry)
  database/     async engine/session, declarative base
  exchange/     Adapter port + ccxt/paper adapters + factory (safety policy)
  models/       SQLAlchemy models
  repositories/ persistence (repository pattern)
  schemas/      Pydantic request/response contracts
  security/     crypto, JWT, TOTP, RBAC
  services/     business logic (service layer)
  trading/      enums + risk calculators
  web/          single-file dashboard
  workers/      Celery app + tasks
tests/          unit + integration tests
```

---

## Disclaimer

This software is provided for educational and research purposes. Cryptocurrency
trading carries substantial financial risk. Nothing here is financial advice.
You are responsible for any live API keys you connect and any trades you enable.

## License

MIT
