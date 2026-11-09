"""Atomic, one-time Redis states for the GitHub App manifest handshake."""

from __future__ import annotations

import json
import secrets
from typing import Any, Protocol

REGISTRATION = "registration"
INSTALLATION = "installation"
MAX_TTL = 900


class StateUnavailable(RuntimeError):
    """Redis is down; callers must fail closed with HTTP 503."""


class StateInvalid(ValueError):
    """The state is unknown, expired, replayed or of the wrong kind."""


class StateClient(Protocol):
    def set(self, name: str, value: str, *, ex: int, nx: bool) -> Any: ...

    def getdel(self, name: str) -> Any: ...


class OneTimeStates:
    def __init__(self, client: StateClient, ttl: int = MAX_TTL) -> None:
        if not 1 <= ttl <= MAX_TTL:
            raise ValueError("state TTL must be at most 15 minutes")
        self.client = client
        self.ttl = ttl

    @staticmethod
    def _key(kind: str, state: str) -> str:
        return f"apps:state:{kind}:{state}"

    def issue(self, kind: str, payload: dict[str, Any]) -> str:
        if kind not in {REGISTRATION, INSTALLATION}:
            raise ValueError("unknown state kind")
        body = json.dumps({**payload, "kind": kind}, sort_keys=True)
        for _ in range(3):
            state = secrets.token_urlsafe(32)
            try:
                stored = self.client.set(
                    self._key(kind, state), body, ex=self.ttl, nx=True
                )
            except Exception as exc:
                raise StateUnavailable("state store unavailable") from exc
            if stored:
                return state
        raise StateUnavailable("state store unavailable")

    def consume(self, kind: str, state: str) -> dict[str, Any]:
        if not state or len(state) > 255:
            raise StateInvalid("state is invalid or expired")
        try:
            value = self.client.getdel(self._key(kind, state))
        except Exception as exc:
            raise StateUnavailable("state store unavailable") from exc
        if value is None:
            raise StateInvalid("state is invalid or expired")
        if isinstance(value, bytes):
            value = value.decode()
        try:
            payload = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise StateInvalid("state is invalid or expired") from exc
        if not isinstance(payload, dict) or payload.get("kind") != kind:
            raise StateInvalid("state is invalid or expired")
        return payload
