"""Persistence helpers for Cloudflare OAuth clients, connections and flows."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from plugins.domains.backend.models import (
    CloudflareConnection,
    CloudflareOAuthClient,
    CloudflareOAuthFlow,
)


def get_oauth_client(db: Session, *, for_update: bool = False) -> CloudflareOAuthClient | None:
    statement = select(CloudflareOAuthClient).where(CloudflareOAuthClient.id == 1)
    if for_update:
        statement = statement.with_for_update()
    return db.scalar(statement)


def get_connection(
    db: Session, tenant_id: str, *, for_update: bool = False
) -> CloudflareConnection | None:
    statement = select(CloudflareConnection).where(
        CloudflareConnection.tenant_id == tenant_id
    )
    if for_update:
        statement = statement.with_for_update()
    return db.scalar(statement)


def list_pending_flows(db: Session, tenant_id: str) -> list[CloudflareOAuthFlow]:
    statement = (
        select(CloudflareOAuthFlow)
        .where(CloudflareOAuthFlow.tenant_id == tenant_id)
        .where(CloudflareOAuthFlow.status == "pending")
        .order_by(CloudflareOAuthFlow.created_at.asc())
    )
    return list(db.scalars(statement).all())


def get_flow_by_state_hash(
    db: Session, state_hash_value: str, *, for_update: bool = False
) -> CloudflareOAuthFlow | None:
    statement = select(CloudflareOAuthFlow).where(
        CloudflareOAuthFlow.state_hash == state_hash_value
    )
    if for_update:
        statement = statement.with_for_update()
    return db.scalar(statement)


def get_flow(db: Session, flow_id: str) -> CloudflareOAuthFlow | None:
    return db.get(CloudflareOAuthFlow, flow_id)


def cancel_pending_flows(
    db: Session, tenant_id: str, *, now: datetime, status: str = "cancelled"
) -> int:
    cancelled = 0
    for flow in list_pending_flows(db, tenant_id):
        flow.status = status
        flow.consumed_at = now
        cancelled += 1
    db.flush()
    return cancelled
