"""Celery application: RabbitMQ broker, durable queues, at-least-once semantics.

- acks_late + task_reject_on_worker_lost: tasks survive worker crashes.
- DLQ queue bound with a dead-letter exchange for poison messages.
- Beat dispatches the transactional outbox every 10 seconds.
"""

from __future__ import annotations

import os
import sys

from celery import Celery
from celery.schedules import crontab

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.config import get_settings  # noqa: E402

settings = get_settings()

broker_url = settings.rabbitmq_url

app = Celery("scout", broker=broker_url, backend=f"rpc://")

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    broker_transport_options={"confirm_publish": True},
    task_default_queue="ingestion",
    task_default_exchange="scout",
    task_default_routing_key="ingestion",
    beat_schedule={
        "dispatch-outbox": {
            "task": "scout.dispatch_outbox",
            "schedule": 10.0,
        },
        "reap-stuck-jobs": {
            "task": "scout.reap_stuck_jobs",
            "schedule": crontab(minute="*/5"),
        },
    },
)

# Declare durable queues with a dead-letter exchange (arg avoids duplicate declaration drift)
from kombu import Exchange, Queue  # noqa: E402

scout_exchange = Exchange("scout", type="direct", durable=True)
dlq_exchange = Exchange("scout.dlx", type="direct", durable=True)

app.conf.task_queues = (
    Queue("ingestion", scout_exchange, routing_key="ingestion", durable=True,
          queue_arguments={"x-dead-letter-exchange": "scout.dlx",
                           "x-dead-letter-routing-key": "ingestion.dlq"}),
    Queue("ingestion.dlq", dlq_exchange, routing_key="ingestion.dlq", durable=True),
)
