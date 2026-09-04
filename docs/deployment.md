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
`beat` (scheduler), `nginx` (reverse proxy).

### The shipped compose file is a development stack

Every port in `docker-compose.yml` is published on `127.0.0.1` only, so nothing
is reachable from outside the host. That is the right default for a laptop — a
wildcard bind would put the development database, with its well-known password,
on whatever network you happen to be attached to.

It also means the stack as shipped serves nobody. Before it becomes a real
deployment:

1. **Expose only the proxy.** Change the `nginx` service to publish
   `"443:443"` (and `"80:80"` to redirect), and **delete the `ports` blocks from
   `db`, `redis`, and `api`** — they only need the internal compose network.
   Publishing Postgres or Redis to the internet is the single most common way a
   stack like this is compromised.
2. **Set real credentials.** `POSTGRES_USER`, `POSTGRES_PASSWORD`, and
   `POSTGRES_DB` are read from the environment with development defaults. Put
   real values in `.env`; do not ship `cth`/`cth`.
3. **Require a Redis password** (`--requirepass`) and update the Redis URLs, or
   use a managed Redis with authentication and TLS.
4. **Prefer managed Postgres and Redis** for anything you cannot afford to lose
   — see §6 and §8.

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

`.github/workflows/ci.yml` runs, on every push and pull request:

- **quality** — ruff lint, ruff format check, mypy, pytest with a 90% coverage
  gate, and `alembic check` (so the schema and the models cannot drift apart)
- **secrets** — gitleaks over the full commit history and the working tree,
  with matches redacted from the build log
- **audit** — `pip-audit --strict` against the Python Packaging Advisory Database
- **docker** — an image build, gated on all three of the above

`.github/workflows/codeql.yml` adds CodeQL analysis on push, on pull request,
and weekly. Dependabot proposes updates for pip, GitHub Actions, and Docker
base images.

Extend the `docker` job to push to your registry and trigger your deploy. Give
it only the permissions it needs — the workflow grants `contents: read` at the
top level, so a publishing job must opt into more for itself.
