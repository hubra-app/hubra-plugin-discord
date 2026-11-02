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
def _connection(connection_id: int, scope: str | tuple[str, ...]):
    """The live connection, inside its club; None when it's gone or has
    none of *scope*."""
    from hubra.core.models.connected_app import ConnectedApp
    from hubra.core.models.tenant import Tenant
    from hubra.core.tenant.context import in_club

    scopes = (scope,) if isinstance(scope, str) else scope
    conn = db.session.get(ConnectedApp, connection_id)
    if (
        conn is None
        or conn.status != "active"
        or not any(apps.has_scope(conn, s) for s in scopes)
    ):
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
def send_announcement(
    self, connection_id: int, body: dict, kind: str | None = None
) -> None:
    from .app import channel_for

    with _connection(connection_id, "announce") as conn:
        if conn is None:
            return
        channel = channel_for(conn.config or {}, kind or "")
        if not channel:
            return
        from hubra.core.tenant.context import current_tenant

        from .profile import brand

        try:
            api.send(channel, brand(body, current_tenant(), conn.config or {}))
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
    with _connection(connection_id, ("roles", "channels")) as conn:
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
        apply_profile.delay(connection_id)
        sync_events.delay(connection_id)
    return len(ids)


@celery_app.task(name="hubra_discord.tasks.apply_profile", bind=True, max_retries=3)
def apply_profile(self, connection_id: int, force: bool = False) -> None:
    """The bot's nickname, avatar and bio on this club's server."""
    from hubra.core.models.connected_app import ConnectedApp
    from hubra.core.models.tenant import Tenant
    from hubra.core.tenant.context import in_club

    from .profile import apply

    conn = db.session.get(ConnectedApp, connection_id)
    if conn is None or conn.status != "active":
        return
    tenant = db.session.get(Tenant, conn.tenant_id)
    if tenant is None or tenant.status != "active":
        return
    with in_club(tenant):
        try:
            problems = apply(conn, tenant, force=force)
        except DiscordError as exc:
            if not exc.retryable:
                _failed(conn, "profile", exc)
                return
            raise self.retry(exc=exc, countdown=exc.retry_after or 60) from exc
        if problems is None:
            return  # nothing changed
        if problems:
            apps.record(conn, "profile", False, "; ".join(problems)[:500])
        else:
            apps.record(conn, "profile", True, "Club look applied")
        db.session.commit()


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


@celery_app.task(
    name="hubra_discord.tasks.send_to_group_channel",
    bind=True,
    max_retries=5,
    acks_late=True,
)
def send_to_group_channel(self, connection_id: int, group_id: int, body: dict) -> None:
    """Post in a group's private channel (its events, polls, reminders)."""
    from .groups import channel_for_group

    with _connection(connection_id, "channels") as conn:
        if conn is None:
            return
        channel = channel_for_group(conn, group_id)
        if not channel:
            return
        from hubra.core.tenant.context import current_tenant

        from .profile import brand

        try:
            api.send(channel, brand(body, current_tenant(), conn.config or {}))
        except DiscordError as exc:
            if not exc.retryable:
                _failed(conn, "announced", exc)
                return
            countdown = exc.retry_after or 2 ** (self.request.retries + 2)
            raise self.retry(exc=exc, countdown=countdown) from exc
        title = (body.get("embeds") or [{}])[0].get("title", "")
        apps.record(conn, "announced", True, title)
        db.session.commit()


@celery_app.task(name="hubra_discord.tasks.sync_events", bind=True, max_retries=3)
def sync_events(self, connection_id: int) -> dict | None:
    """The club's events as the server's scheduled events."""
    from .scheduled import sync

    with _connection(connection_id, "events") as conn:
        if conn is None:
            return None
        try:
            counts = sync(conn)
        except DiscordError as exc:
            db.session.commit()  # keep what was done
            if not exc.retryable:
                _failed(conn, "events", exc)
                return None
            raise self.retry(exc=exc, countdown=exc.retry_after or 60) from exc
        if any(counts.values()):
            apps.record(
                conn,
                "events",
                True,
                ", ".join(f"{n} {k}" for k, n in counts.items() if n),
            )
        db.session.commit()
        return counts


def _platform_joined(guild_id: str, user_id: str) -> str:
    """The platform's own server: Hubra gives the platform roles."""
    try:
        from hubra.core.services import platform_roles
    except ImportError:  # Hubra without platform roles
        return ""
    try:
        return "platform roles" if platform_roles.joined(guild_id, user_id) else ""
    except Exception:  # noqa: BLE001  the next regular sync catches up
        _log.warning("discord: platform roles on join failed", exc_info=True)
        return ""


@celery_app.task(name="hubra_discord.tasks.member_joined", bind=True, max_retries=3)
def member_joined(self, guild_id: str, user_id: str) -> str:
    """From the gateway: someone joined a server (welcome, roles)."""
    from hubra.core.models.tenant import Tenant
    from hubra.core.modules import is_enabled
    from hubra.core.tenant.context import in_club

    from .notify import member_joined as handle

    conn = apps.active("discord", guild_id)
    if conn is None:
        return _platform_joined(guild_id, user_id)
    tenant = db.session.get(Tenant, conn.tenant_id)
    if tenant is None or tenant.status != "active":
        return ""
    with in_club(tenant):
        if not is_enabled("discord"):
            return ""
        try:
            done = handle(conn, user_id)
        except DiscordError as exc:
            if not exc.retryable:
                _failed(conn, "joined", exc)
                return ""
            raise self.retry(exc=exc, countdown=exc.retry_after or 30) from exc
        if done:
            apps.record(conn, "joined", True, done[:500])
        db.session.commit()
        return done
