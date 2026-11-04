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

from . import api, botlog, roles
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


def _failed(conn, action: str, exc: DiscordError, **context) -> None:
    """Log a failure with what was tried, where, and what's missing."""
    from .diagnose import describe

    try:
        text = describe(exc, guild_id=conn.external_id, **context)
    except Exception:  # noqa: BLE001  the plain error is better than none
        _log.warning("discord: could not explain %s", exc, exc_info=True)
        text = str(exc)
    botlog.log(conn, action, False, text)
    db.session.commit()


def _title(body: dict) -> str:
    embeds = body.get("embeds") or [{}]
    return str(embeds[0].get("title") or body.get("content") or "")[:120]


def _posted(conn, action: str, body: dict, channel: str, sent) -> None:
    where = f"channel {channel}"
    if isinstance(sent, dict) and sent.get("id"):
        # A link straight to the message on Discord.
        where = (
            f"https://discord.com/channels/{conn.external_id}/{channel}/{sent['id']}"
        )
    botlog.log(conn, action, True, f"{_title(body)} · {where}")


def _send(
    self,
    conn,
    channel: str,
    body: dict,
    action: str,
    what: str,
    ref: str | None = None,
) -> bool:
    """Post *body* in *channel*, log the result, retry what can be retried.

    *ref* (``e:<event>`` / ``p:<poll>``): remember the message, to keep it
    up to date (live.py)."""
    from hubra.core.tenant.context import current_tenant

    from .profile import brand

    try:
        sent = api.send(channel, brand(body, current_tenant(), conn.config or {}))
    except DiscordError as exc:
        if not exc.retryable:  # no access, channel gone: retrying won't help
            _failed(
                conn,
                action,
                exc,
                doing=f'Posting {what} "{_title(body)}"',
                channel_id=channel,
            )
            return False
        if self.request.retries >= (self.max_retries or 0):
            _failed(conn, action, exc, doing=f'Posting "{_title(body)}"')
            return False
        countdown = exc.retry_after or 2 ** (self.request.retries + 2)
        raise self.retry(exc=exc, countdown=countdown) from exc
    _posted(conn, action, body, channel, sent)
    if ref and isinstance(sent, dict) and sent.get("id"):
        from . import live

        live.remember(conn, ref, channel, sent["id"])
    db.session.commit()
    return True


@celery_app.task(
    name="hubra_discord.tasks.send_announcement",
    bind=True,
    max_retries=5,
    acks_late=True,
)
def send_announcement(
    self,
    connection_id: int,
    body: dict,
    kind: str | None = None,
    ref: str | None = None,
) -> None:
    from .app import channel_for

    with _connection(connection_id, "announce") as conn:
        if conn is None:
            return
        channel = channel_for(conn.config or {}, kind or "")
        if not channel:
            botlog.skipped(
                conn,
                _title(body) or (kind or "message"),
                "no channel picked (Settings › Verbundene Apps › Discord)",
            )
            db.session.commit()
            return
        _send(self, conn, channel, body, "announced", kind or "", ref)


def _role_task(self, connection_id: int, action, *args):
    with _connection(connection_id, ("roles", "channels")) as conn:
        if conn is None:
            return None
        try:
            result = action(conn, *args)
        except DiscordError as exc:
            if not exc.retryable:
                _failed(conn, "roles", exc, doing="Updating roles")
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
        botlog.log(conn, "roles", True, detail)
    return detail


def _remove_all(conn, discord_user_id: str):
    detail = roles.apply(conn, discord_user_id, set(), "account unlinked")
    if detail:
        botlog.log(conn, "roles", True, detail)
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
        audit_connection.delay(connection_id)
        daily_extras.delay(connection_id)
    if ids:
        # New commands of a plugin update (such as "Melden") reach every
        # server by the next night.
        register_commands.delay()
    return len(ids)


@celery_app.task(name="hubra_discord.tasks.daily_extras", bind=True, max_retries=3)
def daily_extras(self, connection_id: int) -> dict | None:
    """Nightly: the stats channel's count, and people who left the club
    leave the server (stats.py, leavers.py)."""
    from . import leavers, stats

    every = ("channels", "moderate")
    with _connection(connection_id, every) as conn:
        if conn is None:
            return None
        done: dict = {}
        try:
            done["stats"] = stats.ensure(conn)
        except DiscordError as exc:
            _failed(conn, "stats", exc, doing="Updating the stats channel")
        else:
            if done["stats"]:
                botlog.log(conn, "stats", True, done["stats"])
        if (conn.config or {}).get("kick_leavers"):
            done.update(leavers.run(conn))
        db.session.commit()
        return done


@celery_app.task(name="hubra_discord.tasks.hourly")
def hourly() -> int:
    """Every hour: Monday's posts (digest.py) and feedback questions after
    events (feedback.py)."""
    from sqlalchemy import select

    from hubra.core.models.connected_app import ConnectedApp

    from . import digest, feedback

    sent = 0
    ids = db.session.scalars(
        select(ConnectedApp.id).where(
            ConnectedApp.provider == "discord", ConnectedApp.status == "active"
        )
    ).all()
    for connection_id in ids:
        every = ("announce", "channels", "commands", "moderate")
        with _connection(connection_id, every) as conn:
            if conn is None:
                continue
            try:
                sent += _weekly(conn, digest) + _ask_feedback(conn, feedback)
            except DiscordError as exc:
                _failed(conn, "weekly", exc, doing="Monday posts or feedback")
            db.session.commit()
    return sent


def _weekly(conn, digest) -> int:
    from .app import channel_for
    from .mod import channel as mod_channel
    from .profile import brand
    from .stats import numbers, weekly_embed
    from hubra.core.tenant.context import current_tenant

    if not digest.due(conn):
        return 0
    config = conn.config or {}
    digest.mark_sent(conn)  # once, even when a post fails
    db.session.commit()
    sent = 0
    if config.get("weekly_events") and apps.has_scope(conn, "announce"):
        item = digest.events_embed(conn)
        channel = channel_for(config, "events")
        if item and channel:
            body = brand(
                {"embeds": [item], "allowed_mentions": {"parse": []}},
                current_tenant(),
                config,
            )
            api.send(channel, body)
            botlog.log(conn, "weekly", True, "Wochenvorschau gepostet")
            sent += 1
    if config.get("weekly_stats") and mod_channel(conn):
        api.send(
            mod_channel(conn),
            {"embeds": [weekly_embed(numbers())], "allowed_mentions": {"parse": []}},
        )
        sent += 1
    return sent


def _ask_feedback(conn, feedback) -> int:
    from .profile import brand
    from hubra.core.tenant.context import current_tenant

    sent = 0
    for event in feedback.due(conn):
        feedback.mark_asked(conn, event)  # asked once, even if it fails
        db.session.commit()
        channel = feedback.where(conn, event)
        if not channel:
            botlog.skipped(conn, f'Feedback "{event.title}"', "no channel for events")
            continue
        api.send(
            channel,
            brand(feedback.question(event), current_tenant(), conn.config or {}),
        )
        botlog.log(conn, "feedback", True, f'Rückmeldung erbeten: "{event.title}"')
        sent += 1
    return sent


@celery_app.task(name="hubra_discord.tasks.audit_connection")
def audit_connection(connection_id: int) -> int:
    """Nightly permission check of the club's server; new findings go to
    the bot log (audit.py)."""
    from .audit import nightly

    every = ("announce", "channels", "roles", "commands", "events", "notify")
    with _connection(connection_id, every) as conn:
        if conn is None:
            return 0
        found = nightly(conn)
        db.session.commit()
        return found


@celery_app.task(name="hubra_discord.tasks.send_dm_broadcast")
def send_dm_broadcast(
    connection_id: int, user_ids: list, title: str, text: str, who: str = ""
) -> dict | None:
    """A club admin's direct message to a set of members (dm.py)."""
    from .dm import send_all

    with _connection(connection_id, "notify") as conn:
        if conn is None:
            return None
        result = send_all(conn, [str(u) for u in user_ids], title, text, who)
        db.session.commit()
        return result


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
                _failed(conn, "profile", exc, doing="Setting the bot's name and logo")
                return
            raise self.retry(exc=exc, countdown=exc.retry_after or 60) from exc
        if problems is None:
            return  # nothing changed
        if problems:
            botlog.log(conn, "profile", False, "; ".join(problems)[:500])
        else:
            botlog.log(conn, "profile", True, "Club look applied")
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
def send_to_group_channel(
    self,
    connection_id: int,
    group_id: int | None,
    body: dict,
    ref: str | None = None,
    source: str | None = None,
) -> None:
    """Post in a group's or function's private channel (its events, polls,
    reminders)."""
    from .groups import channel_for_source

    source = source or f"group:{group_id}"
    with _connection(connection_id, "channels") as conn:
        if conn is None:
            return
        channel = channel_for_source(conn, source)
        if not channel:
            botlog.skipped(conn, _title(body), f"{source} has no channel")
            db.session.commit()
            return
        _send(self, conn, channel, body, "announced", "in the group channel", ref)


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
                _failed(conn, "events", exc, doing="Updating the server's Events list")
                return None
            raise self.retry(exc=exc, countdown=exc.retry_after or 60) from exc
        if any(counts.values()):
            botlog.log(
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
                _failed(conn, "joined", exc, doing="Welcoming a new server member")
                return ""
            raise self.retry(exc=exc, countdown=exc.retry_after or 30) from exc
        if done:
            botlog.log(conn, "joined", True, done[:500])
        db.session.commit()
        return done


@celery_app.task(name="hubra_discord.tasks.post_log", bind=True, max_retries=3)
def post_log(self, connection_id: int, action: str, ok: bool, detail: str) -> None:
    """One entry of the bot log in the club's log channel on Discord."""
    from hubra.core.models.connected_app import ConnectedApp

    conn = db.session.get(ConnectedApp, connection_id)
    if conn is None or conn.status != "active":
        return
    channel = (conn.config or {}).get("log_channel_id")
    if not channel:
        return
    body = {
        "embeds": [botlog.embed(action, ok, detail)],
        "allowed_mentions": {"parse": []},
    }
    try:
        api.send(channel, body)
    except DiscordError as exc:
        if exc.retryable and self.request.retries < (self.max_retries or 0):
            raise self.retry(exc=exc, countdown=exc.retry_after or 30) from exc
        from .diagnose import describe

        try:
            text = describe(
                exc,
                doing="Writing to the log channel",
                guild_id=conn.external_id,
                channel_id=channel,
            )
        except Exception:  # noqa: BLE001
            text = f"Writing to the log channel failed: {exc}"
        # Straight to Hubra's list, never back to the channel (no loop).
        apps.record(conn, "log", False, text)
        _log.warning("discord club=%s log channel: %s", conn.tenant_id, text)
        db.session.commit()


# ── Messages kept up to date (live.py) ───────────────────────────────────────


def _live_task(self, connection_id: int, what: str, fn, *args):
    with _connection(connection_id, ("announce", "channels")) as conn:
        if conn is None:
            return None
        try:
            done = fn(conn, *args)
        except DiscordError as exc:
            db.session.commit()
            if not exc.retryable:
                _failed(conn, "updated", exc, doing=what)
                return None
            raise self.retry(exc=exc, countdown=exc.retry_after or 30) from exc
        db.session.commit()
        return done


@celery_app.task(name="hubra_discord.tasks.refresh_event", bind=True, max_retries=3)
def refresh_event(self, connection_id: int, event_id: int, deleted: bool = False):
    """An event's messages: places, a change, or "Abgesagt"."""
    from . import live

    return _live_task(
        self,
        connection_id,
        "Updating an event message",
        live.refresh_event,
        event_id,
        deleted,
    )


@celery_app.task(name="hubra_discord.tasks.finish_poll", bind=True, max_retries=3)
def finish_poll(self, connection_id: int, poll_id: int):
    """A closed poll's messages show the result."""
    from . import live

    return _live_task(
        self, connection_id, "Showing a poll result", live.finish_poll, poll_id
    )


@celery_app.task(name="hubra_discord.tasks.refresh_training", bind=True, max_retries=3)
def refresh_training(self, connection_id: int, event_id: int):
    """A training card's count, or its final state once it is over."""
    from . import training

    return _live_task(
        self, connection_id, "Updating a training card", training.refresh, event_id
    )


@celery_app.task(name="hubra_discord.tasks.tidy_messages")
def tidy_messages() -> int:
    """Every few minutes: polls whose closing time passed get their result,
    events that are over lose their buttons."""
    from sqlalchemy import select

    from hubra.core.models.connected_app import ConnectedApp

    from . import live, training

    queued = 0
    conns = db.session.scalars(
        select(ConnectedApp).where(
            ConnectedApp.provider == "discord", ConnectedApp.status == "active"
        )
    ).all()
    for conn in conns:
        if not live.posted(conn):
            continue
        with _connection(conn.id, ("announce", "channels")) as live_conn:
            if live_conn is None:
                continue
            for poll_id in live.due_polls(live_conn):
                finish_poll.delay(conn.id, poll_id)
                queued += 1
            for event_id in live.over_events(live_conn):
                refresh_event.delay(conn.id, event_id)
                queued += 1
            for event_id in training.over(live_conn):
                refresh_training.delay(conn.id, event_id)
                queued += 1
            db.session.commit()  # polls deleted in the app are forgotten
    return queued
