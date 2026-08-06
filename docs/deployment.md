# Deployment guide

## Prerequisites

- Docker + Docker Compose (or a Python 3.11+ host with PostgreSQL 14+ and Redis 6+).
- Secrets: `MASTER_ENCRYPTION_KEY`, `JWT_SECRET_KEY` (generate with
  `python -m app.scripts.gen_keys`).

## 1. Configure

```bash
cp .env.example .env
python -m app.scripts.gen_keys      # paste the two keys into .env
```

Set for production:

```
APP_ENV=production
APP_DEBUG=false
ALLOW_LIVE_TRADING=false            # flip to true only when you are ready
USE_EXCHANGE_TESTNET=true
CORS_ORIGINS=https://your-frontend.example
```

## 2. Run with Docker Compose

```bash
docker compose up --build -d
docker compose ps
docker compose logs -f api
```

Services: `db` (Postgres), `redis`, `api` (uvicorn), `worker` (Celery),
`beat` (scheduler), `nginx` (reverse proxy on :80).

## 3. Database migrations

The API auto-creates tables only in non-production. In production, use Alembic:

```bash
docker compose exec api alembic revision --autogenerate -m "init"
docker compose exec api alembic upgrade head
```

## 4. TLS / HTTPS

Terminate TLS at Nginx. Provide certificates (e.g. via Let's Encrypt) and
enable the 443 server block in `deploy/nginx.conf`. HSTS is already emitted by
the application.

## 5. Health & readiness

- Liveness: `GET /api/v1/health`
- Readiness: `GET /api/v1/ready` (reports environment, live-trading flag, testnet)
- The Docker image ships a `HEALTHCHECK` hitting the liveness probe.

## 6. Scaling

- Run multiple `api` replicas behind Nginx/your load balancer.
- Run additional `worker` replicas for background throughput.
- Move rate limiting to a Redis-backed limiter when running multiple replicas.
- Use a managed PostgreSQL and Redis for durability and backups.

## 7. Observability

- Logs are structured JSON in production — ship them to your log stack.
- Add your APM/metrics exporter of choice; request timing is already emitted via
  the `x-response-time-ms` header and per-request logging.

## 8. Backups & DR

- Back up PostgreSQL regularly (`pg_dump` / managed snapshots).
- **Back up `MASTER_ENCRYPTION_KEY` securely** — without it, encrypted exchange
  credentials cannot be recovered.

## CI/CD

`.github/workflows/ci.yml` runs lint, format check, type check, tests with a
coverage gate, and a Docker build on every push/PR. Extend the `docker` job to
push to your registry and trigger your deploy.
