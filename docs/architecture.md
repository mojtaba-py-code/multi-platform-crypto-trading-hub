# Architecture

## Guiding principles

- **Clean layering.** Dependencies point inward: `api → services → repositories
  → models`. The domain (services, exchange port, risk math) never imports web
  or ORM specifics.
- **Dependency inversion.** Services depend on the `ExchangeAdapter` abstraction
  and repository classes, not on ccxt or SQL. Concrete implementations are
  injected (`app/api/deps.py`).
- **Single responsibility.** Each module does one thing: encryption in
  `security/crypto.py`, token logic in `security/tokens.py`, order placement in
  `services/trading_service.py`, and so on.

## Layers

### API layer (`app/api`)
Thin FastAPI routers translate HTTP to service calls and back. Cross-cutting
concerns live in middleware (request id, security headers, rate limiting) and a
central exception-to-JSON mapper. Dependency injection wires a fresh unit of
work (database session) per request.

### Service layer (`app/services`)
Business logic and orchestration. Services own the transaction boundary
(the request-scoped session commits on success, rolls back on error). Examples:

- `AuthService` — registration, login (+ 2FA), token issuance, TOTP enrolment.
- `AccountService` — encrypts credentials, builds adapters via the factory.
- `TradingService` — pre-trade risk checks, order placement, persistence.
- `MarketService` — public market data and indicator computation.
- `PortfolioService` — cross-account balance aggregation with failure isolation.

### Repository layer (`app/repositories`)
Encapsulates persistence with a small generic base plus per-aggregate
repositories. Repositories `flush` but never `commit`.

### Exchange layer (`app/exchange`)
The heart of the multi-exchange design:

- `base.py` defines the `ExchangeAdapter` **port** and a read-only
  `MarketDataSource` protocol.
- `ccxt_adapter.py` is the ccxt-backed **adapter** — it normalises every
  response into domain DTOs and maps ccxt errors into the app's exception
  hierarchy, guarded by a circuit breaker.
- `paper.py` simulates trading against any `MarketDataSource`.
- `factory.py` is the **policy choke point**: it decides paper vs live and
  testnet vs mainnet. Client requests cannot widen this.
- `registry.py` is the single source of truth for supported exchanges.

## Request lifecycle (place order)

```
POST /trading/orders
  → RequestContext middleware (request id, headers)
  → RateLimiter middleware
  → require(order:create) dependency (RBAC)
  → get_current_user (decode JWT access token)
  → TradingService.place_order
       → AccountService.get_account          (ownership check)
       → AccountService.build_adapter        (factory → paper/live)
       → RiskLimits.check_order              (leverage / size)
       → adapter.create_order                (paper fill or live send)
       → OrderRepository.add                 (persist)
       → AuditRepository.record              (audit trail)
  → session.commit (unit of work)
  → OrderOut response (+ security headers, request id)
```

## Design patterns in use

| Pattern | Where |
| --- | --- |
| Adapter / Port | `ExchangeAdapter`, `CcxtAdapter`, `PaperAdapter` |
| Factory | `ExchangeFactory` |
| Repository | `app/repositories/*` |
| Service layer | `app/services/*` |
| Dependency injection | `app/api/deps.py` |
| Strategy (implicit) | indicator dispatch, fill-price resolution |
| Circuit breaker / Retry | `app/core/resilience.py` |

## Extending: add an exchange

1. Confirm ccxt supports it.
2. Add one `ExchangeInfo(...)` line to `SUPPORTED_EXCHANGES` in `registry.py`.
3. Done — the factory, adapters, schemas and API pick it up automatically.
