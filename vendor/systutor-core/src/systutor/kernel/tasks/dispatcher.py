from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from systutor.core.config import Settings
from systutor.core.errors import AppError
from systutor.kernel.tasks.broker import configure_dramatiq_broker


class TaskDispatcherUnavailableError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=503, code="task_dispatcher_unavailable")


@dataclass(slots=True)
class TaskDispatcher:
    broker: Any | None
    reason: str | None = None

    @property
    def is_available(self) -> bool:
        return self.broker is not None

    def enqueue(self, task_name: str, payload: dict) -> str:
        if self.broker is None:
            raise TaskDispatcherUnavailableError(self.reason or "task dispatcher is not configured")

        dramatiq = importlib.import_module("dramatiq")
        message = dramatiq.Message(
            queue_name="default",
            actor_name=task_name,
            args=(),
            kwargs={"payload": dict(payload)},
            options={},
            message_id=str(uuid4()),
            message_timestamp=None,
        )
        self.broker.enqueue(message)
        return message.message_id


def build_task_dispatcher(settings: Settings) -> TaskDispatcher:
    try:
        broker = configure_dramatiq_broker(settings)
    except Exception as exc:  # pragma: no cover - depends on runtime env
        return TaskDispatcher(broker=None, reason=str(exc))
    return TaskDispatcher(broker=broker)
