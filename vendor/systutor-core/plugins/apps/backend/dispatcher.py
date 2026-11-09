"""Dramatiq dispatcher whose message body contains only operation_id."""

from __future__ import annotations

import importlib


class DramatiqDispatcher:
    def enqueue(self, operation_id: str) -> str:
        dramatiq = importlib.import_module("dramatiq")
        message = dramatiq.Message(
            queue_name="apps",
            actor_name="apps_operation",
            args=(operation_id,),
            kwargs={},
            options={"max_retries": 0},
        )
        broker = dramatiq.get_broker()
        broker.enqueue(message)
        return message.message_id
