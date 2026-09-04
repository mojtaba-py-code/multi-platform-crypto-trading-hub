## What this changes

<!-- What it does and why. Link the issue it closes, if there is one. -->

## How it was verified

<!-- The commands you ran, and anything you checked by hand. -->

- [ ] `ruff check alembic app tests` and `ruff format --check alembic app tests`
- [ ] `mypy app`
- [ ] `pytest --cov=app` — coverage stays at or above 90%
- [ ] New behaviour is covered by a test
- [ ] `alembic revision --autogenerate` run, if a model changed

## Safety checklist

- [ ] Paper trading is still the default; the `ExchangeFactory` choke point is intact
- [ ] No credential is logged, returned in a response, or stored in plaintext
- [ ] **No secret is committed** — not in a test, fixture, `.env.example`, or workflow
- [ ] Error responses still reveal nothing about internals
- [ ] The production startup gate was not loosened
