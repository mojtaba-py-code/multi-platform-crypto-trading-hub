# Contributing

Thanks for taking an interest. This is a security-sensitive project — it holds
exchange API credentials — so the bar for changes is deliberately high.

**Found a security issue? Do not open an issue.** Follow
[`SECURITY.md`](SECURITY.md) instead.

## Getting set up

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   |   Unix: source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
python -m app.scripts.gen_keys      # paste the output into .env
```

No database is needed to run the tests — they use in-memory SQLite.

## Before you open a pull request

```bash
make check     # ruff + mypy + pytest with coverage
```

or individually:

```bash
ruff check alembic app tests
ruff format alembic app tests
mypy app
pytest --cov=app --cov-report=term-missing
```

CI runs all of the above plus a secret scan, a dependency audit, CodeQL, and a
Docker build. It enforces a **90% coverage floor**; the project currently sits
at 95%, so please do not spend that headroom without a reason.

If you change a SQLAlchemy model, generate the matching migration — CI runs
`alembic check` and will fail if the schema and the models have drifted:

```bash
alembic revision --autogenerate -m "describe the change"
```

## What the code should look like

The layering is strict: **routers → services → repositories → models**. A router
should not touch SQL, and a service should depend on the `ExchangeAdapter`
abstraction rather than on ccxt.

- Type annotations on new public functions; `mypy app` must stay clean.
- Comments explain *why*, not *what*. If a line is surprising, say what would
  break without it.
- Tests are named for the behaviour they pin, and their docstring says why that
  behaviour matters. See `tests/test_production_guardrails.py` for the house style.

## Rules that exist for safety

These are not stylistic preferences. A pull request that weakens one of them
will not be merged without a very good argument.

1. **Paper trading stays the default.** `ALLOW_LIVE_TRADING=false` must keep
   every order simulated. The check belongs in `ExchangeFactory` — the single
   choke point — and must not become reachable from a request parameter.
2. **No plaintext credential ever reaches storage or a log.** Encrypt through
   `SecretCipher`; if you add a field that holds a secret, add its key to
   `_SENSITIVE_KEYS` in `app/core/logging.py` and keep it out of the response
   schema.
3. **No secret, real or disposable, goes into the repository.** Not in a test,
   not in a fixture, not in `.env.example`, not in a workflow file. Generate
   them at runtime — see how `tests/` and `ci.yml` do it.
4. **Error responses stay opaque.** A 500 returns `internal_error` and nothing
   about the internals.
5. **The production startup gate only gets stricter.** Each rejection in
   `Settings.validate_runtime` is pinned by a test; add to it rather than
   loosening it.

## Adding an exchange

Usually a one-line entry in `app/exchange/registry.py`, since the adapter goes
through ccxt. Set `requires_passphrase=True` if the exchange needs one, and add
a case to `tests/test_factory.py`.

## Commit messages

Present tense, describing the effect: *"Reject wildcard CORS in production"*
rather than *"fixed cors"*. Reference an issue when there is one.

## License

Contributions are accepted under the [MIT License](LICENSE).
