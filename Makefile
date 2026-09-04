.PHONY: install dev test cov lint format type check run worker beat up down keys lock

install:
	pip install -e ".[dev]"

test:
	pytest -q

cov:
	pytest --cov=app --cov-report=term-missing

lint:
	ruff check app tests

format:
	ruff format app tests

type:
	mypy app

check: lint type cov

run:
	uvicorn app.main:app --reload

worker:
	celery -A app.workers.celery_app.celery worker --loglevel=info

beat:
	celery -A app.workers.celery_app.celery beat --loglevel=info

up:
	docker compose up --build

down:
	docker compose down

keys:
	python -m app.scripts.gen_keys

# Recompile the pinned runtime lock after editing dependencies in pyproject.toml.
lock:
	uv pip compile pyproject.toml --universal --python-version 3.12 --no-header --output-file requirements.txt
