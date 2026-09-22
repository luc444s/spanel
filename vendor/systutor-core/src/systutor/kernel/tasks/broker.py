from __future__ import annotations

import importlib
from typing import Any

from systutor.core.config import Settings


def build_dramatiq_broker(settings: Settings) -> Any:
    redis_module = importlib.import_module("dramatiq.brokers.redis")
    redis_broker = redis_module.RedisBroker
    return redis_broker(url=settings.redis_url)


def configure_dramatiq_broker(settings: Settings) -> Any:
    dramatiq = importlib.import_module("dramatiq")
    broker = build_dramatiq_broker(settings)
    dramatiq.set_broker(broker)
    return broker
