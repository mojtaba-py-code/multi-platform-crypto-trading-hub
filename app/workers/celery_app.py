"""Celery application and beat schedule.

Background jobs keep local state in sync with exchanges (balances, prices,
orders) and run housekeeping. The Celery app is configured from the same
:class:`Settings` as the API so broker/result URLs stay consistent.

Run a worker:
    celery -A app.workers.celery_app.celery worker --loglevel=info
Run the scheduler:
    celery -A app.workers.celery_app.celery beat --loglevel=info
"""

from __future__ import annotations

from celery import Celery

from app.config import get_settings

settings = get_settings()

celery = Celery(
    "crypto_trading_hub",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)

celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_time_limit=120,
    task_soft_time_limit=90,
)

# Periodic schedule (seconds). Tuned conservatively to respect exchange limits.
celery.conf.beat_schedule = {
    "sync-portfolios": {
        "task": "app.workers.tasks.sync_all_portfolios",
        "schedule": 60.0,
    },
    "evaluate-alerts": {
        "task": "app.workers.tasks.evaluate_alerts",
        "schedule": 30.0,
    },
    "cleanup-audit-logs": {
        "task": "app.workers.tasks.cleanup_old_audit_logs",
        "schedule": 3600.0,
    },
}
