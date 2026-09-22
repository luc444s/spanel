from __future__ import annotations

import logging

from systutor.sdk.context import PluginContext

logger = logging.getLogger(__name__)


def audit_account_created(
    context: PluginContext,
    tenant_id: str,
    actor_user_id: str,
    target_email: str,
) -> None:
    context.publish_event(
        event_name="mail.account.created",
        payload={"target_email": target_email},
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        entity_type="mail_account",
        entity_id=target_email,
    )


def audit_password_changed(
    context: PluginContext,
    tenant_id: str,
    actor_user_id: str,
    target_email: str,
) -> None:
    context.publish_event(
        event_name="mail.account.password_changed",
        payload={"target_email": target_email},
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        entity_type="mail_account",
        entity_id=target_email,
    )
