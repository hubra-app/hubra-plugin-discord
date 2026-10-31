"""Celery tasks: post announcements and keep Discord roles in sync.

Each task gets a connection id and looks the connection up itself, so a
connection disconnected meanwhile does nothing, and no token or URL ever
sits in the queue.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager

from hubra.core import apps
from hubra.core.extensions import db
from hubra.core.tasks import celery_app

from . import api, roles
from .api import DiscordError

_log = logging.getLogger(__name__)


@contextmanager
def _connection(connection_id: int, scope: str):
    """The live connection, inside its club; None when it's gone or lacks
    *scope*."""
    from hubra.core.models.connected_app import ConnectedApp
    from hubra.core.models.tenant import Tenant
    from hubra.core.tenant.context import in_club

    conn = db.session.get(ConnectedApp, connection_id)
    if conn is None or conn.status != "active" or not apps.has_scope(conn, scope):
        yield None
        return
    tenant = db.session.get(Tenant, conn.tenant_id)
    if tenant is None or tenant.status != "active":
        yield None
        return
    with in_club(tenant):
        yield conn


def _failed(conn, action: str, exc: DiscordError) -> None:
    from .app import _explain

    apps.record(conn, action, False, _explain(exc))
    db.session.commit()


@celery_app.task(
    name="hubra_discord.tasks.send_announcement",
    bind=True,
    max_retries=5,
    acks_late=True,
)
def send_announcement(self, connection_id: int, body: dict) -> None:
    with _connection(connection_id, "announce") as conn:
        if conn is None:
            return
        channel = (conn.config or {}).get("channel_id")
        if not channel:
            return
        try:
            api.send(channel, body)
        except DiscordError as exc:
            if not exc.retryable:  # no access, channel gone: retrying won't help
                _failed(conn, "announced", exc)
                return
            countdown = exc.retry_after or 2 ** (self.request.retries + 2)
            raise self.retry(exc=exc, countdown=countdown) from exc
        title = (body.get("embeds") or [{}])[0].get("title", "")
        apps.record(conn, "announced", True, title)
        db.session.commit()


def _role_task(self, connection_id: int, action, *args):
    with _connection(connection_id, "roles") as conn:
        if conn is None:
            return None
        try:
            result = action(conn, *args)
        except DiscordError as exc:
            if not exc.retryable:
                _failed(conn, "roles", exc)
                return None
            countdown = exc.retry_after or 2 ** (self.request.retries + 2)
            raise self.retry(exc=exc, countdown=countdown) from exc
        db.session.commit()
        return result


def _sync_one(conn, member_id: int):
    from hubra.core.models.member import Member

    member = db.session.get(Member, member_id)
    if member is None:
        return None
    detail = roles.sync_member(conn, member)
    if detail:
        apps.record(conn, "roles", True, detail)
    return detail


def _remove_all(conn, discord_user_id: str):
    detail = roles.apply(conn, discord_user_id, set(), "account unlinked")
    if detail:
        apps.record(conn, "roles", True, detail)
    return detail


@celery_app.task(name="hubra_discord.tasks.sync_member", bind=True, max_retries=5)
def sync_member(self, connection_id: int, member_id: int):
    return _role_task(self, connection_id, _sync_one, member_id)


@celery_app.task(name="hubra_discord.tasks.remove_roles", bind=True, max_retries=5)
def remove_roles(self, connection_id: int, discord_user_id: str):
    return _role_task(self, connection_id, _remove_all, discord_user_id)


@celery_app.task(name="hubra_discord.tasks.sync_connection", bind=True, max_retries=3)
def sync_connection(self, connection_id: int):
    return _role_task(self, connection_id, roles.sync_all)


@celery_app.task(name="hubra_discord.tasks.sync_all_clubs")
def sync_all_clubs() -> int:
    """Nightly: fix roles that drifted (archived or suspended members, tier
    and function changes, roles changed by hand on Discord)."""
    from sqlalchemy import select

    from hubra.core.models.connected_app import ConnectedApp

    ids = db.session.scalars(
        select(ConnectedApp.id).where(
            ConnectedApp.provider == "discord", ConnectedApp.status == "active"
        )
    ).all()
    for connection_id in ids:
        sync_connection.delay(connection_id)
    return len(ids)


@celery_app.task(name="hubra_discord.tasks.register_commands", bind=True, max_retries=5)
def register_commands(self) -> None:
    """Install the slash commands for the Hubra application (idempotent)."""
    from .commands import register

    try:
        register()
    except DiscordError as exc:
        if not exc.retryable:
            _log.warning("discord: could not register commands: %s", exc)
            return
        raise self.retry(exc=exc, countdown=exc.retry_after or 30) from exc
