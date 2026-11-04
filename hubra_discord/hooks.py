"""Event handlers: announcements and role changes for the club's Discord.

Only confirmed connections (``hubra.core.apps``) with the matching scope do
anything. Work is queued after the request's commit, so a change that is
rolled back is never announced, and the queue carries a connection id, never
a token.
"""

from __future__ import annotations

import logging

from sqlalchemy import event as sa_event

from hubra.core import apps
from hubra.core.extensions import db

from . import botlog, live

from .commands import event_buttons
from .messages import (
    event_embed,
    event_reminder_embed,
    payload,
    poll_closing_embed,
    poll_embed,
    post_embed,
)

_log = logging.getLogger(__name__)


# ── After the commit ─────────────────────────────────────────────────────────


def after_commit(fn, *args) -> None:
    """Call ``fn(*args)`` once the current transaction is committed."""
    session = db.session()
    if not session.info.get("discord_listening"):
        sa_event.listen(session, "after_commit", _run_pending)
        sa_event.listen(session, "after_soft_rollback", _drop_pending)
        session.info["discord_listening"] = True
    pending = session.info.setdefault("discord_pending", [])
    if (fn, args) not in pending:  # a series change asks once per date
        pending.append((fn, args))


def _run_pending(session) -> None:
    pending = session.info.pop("discord_pending", None)
    for fn, args in pending or ():
        try:
            fn(*args)
        except Exception:
            # The commit is done; a broker outage must not turn it into a 500.
            _log.exception("discord: could not queue work after the commit")


def _drop_pending(session, previous_transaction) -> None:
    if not previous_transaction.nested:  # a savepoint leaves the rest alive
        session.info.pop("discord_pending", None)


def _connections(tenant_id, scope: str):
    if tenant_id is None:
        return []
    return [
        c for c in apps.for_tenant(tenant_id, "discord") if apps.has_scope(c, scope)
    ]


# ── Announcements ────────────────────────────────────────────────────────────


SWITCH_NAMES = {
    "announce_events": "Neue Anlässe posten",
    "announce_posts": "Neue Beiträge posten",
    "announce_polls": "Neue Umfragen posten",
    "announce_reminders": "Erinnerungen posten",
}


def _announce(
    tenant_id, kind: str, toggle: str, build, what: str = "", ref: str | None = None
) -> None:
    from .app import channel_for
    from .tasks import send_announcement

    for conn in _connections(tenant_id, "announce"):
        config = conn.config or {}
        if not config.get(toggle):
            botlog.skipped(
                conn, what or kind, f'"{SWITCH_NAMES.get(toggle, toggle)}" is off'
            )
            continue
        if not channel_for(config, kind):
            botlog.skipped(
                conn, what or kind, f"no channel picked for {kind} or the main channel"
            )
            continue
        built = build(conn)
        if built is None:
            botlog.skipped(
                conn,
                what or kind,
                'members-only event; switch on "Auch Anlässe nur für Mitglieder" '
                "to post members-only events",
            )
            continue
        item, components = built if isinstance(built, tuple) else (built, None)
        body = payload(config.get("mention"), [item], components)
        after_commit(send_announcement.delay, conn.id, body, kind, ref)


def _to_groups(
    tenant_id, group_ids, build, what: str = "", ref: str | None = None
) -> None:
    """Post in the private channels of these groups (``channels`` scope)."""
    from .groups import channel_for_group
    from .tasks import send_to_group_channel

    for conn in _connections(tenant_id, "channels"):
        if not (conn.config or {}).get("group_announce", True):
            botlog.skipped(conn, what, "posting in group channels is off")
            continue
        if not any(channel_for_group(conn, gid) for gid in group_ids or ()):
            botlog.skipped(
                conn, what, "only for some groups, and none of them has a channel"
            )
        for gid in group_ids or ():
            if not channel_for_group(conn, gid):
                continue
            item, components = build(conn)
            body = payload(None, [item], components)
            after_commit(send_to_group_channel.delay, conn.id, int(gid), body, ref)


def _has_group_channel(tenant_id, group_id) -> bool:
    from .groups import channel_for_group

    return group_id is not None and any(
        channel_for_group(c, group_id) for c in _connections(tenant_id, "channels")
    )


def _sync_events_later(tenant_id) -> None:
    from .tasks import sync_events

    for conn in _connections(tenant_id, "events"):
        after_commit(sync_events.delay, conn.id)


def on_event_published(tenant_id=None, event_id=None, **_):
    from hubra.core.models.event import ClubEvent

    event = db.session.get(ClubEvent, event_id)
    if event is None:
        return
    _sync_events_later(tenant_id)

    def buttons_for(conn):
        return event_buttons(event) if apps.has_scope(conn, "commands") else None

    if _has_group_channel(tenant_id, event.group_id):
        _to_groups(
            tenant_id,
            [event.group_id],
            lambda c: (event_embed(event), buttons_for(c)),
            f'Event "{event.title}"',
            live.event_key(event.id),
        )
        return  # a group's event goes to its channel, not the whole server

    def build(conn):
        if not _members_only_ok(event, conn):
            return None
        return event_embed(event), buttons_for(conn)

    _announce(
        tenant_id,
        "events",
        "announce_events",
        build,
        f'Event "{event.title}"',
        live.event_key(event.id),
    )


def _refresh_later(tenant_id, event_id, deleted: bool = False) -> None:
    """Edit the event's messages once the change is committed."""
    from .tasks import refresh_event

    key = live.event_key(event_id)
    for conn in _live_connections(tenant_id):
        if key in live.posted(conn):
            after_commit(refresh_event.delay, conn.id, int(event_id), bool(deleted))


def _live_connections(tenant_id):
    seen, out = set(), []
    for scope in ("announce", "channels"):
        for conn in _connections(tenant_id, scope):
            if conn.id not in seen:
                seen.add(conn.id)
                out.append(conn)
    return out


def on_event_changed(tenant_id=None, event_id=None, deleted=False, **_):
    _sync_events_later(tenant_id)
    if event_id is not None:
        _refresh_later(tenant_id, event_id, deleted)


def on_event_signups_changed(tenant_id=None, event_id=None, **_):
    """Places taken changed: the event's messages show the new count."""
    if event_id is not None:
        _refresh_later(tenant_id, event_id)


def on_poll_closed(tenant_id=None, poll_id=None, **_):
    """Closed early: show the result now (tidy_messages catches the polls
    that close on their own)."""
    from .tasks import finish_poll

    if poll_id is None:
        return
    key = live.poll_key(poll_id)
    for conn in _live_connections(tenant_id):
        if key in live.posted(conn):
            after_commit(finish_poll.delay, conn.id, int(poll_id))


def on_post_published(tenant_id=None, post_id=None, **_):
    from hubra.core.models.post import Post

    post = db.session.get(Post, post_id)
    if post is not None:
        _announce(
            tenant_id,
            "posts",
            "announce_posts",
            lambda _conn: post_embed(post),
            f'Post "{post.title}"',
        )


def _poll_buttons(conn, poll):
    from .polls import buttons

    config = conn.config or {}
    if not apps.has_scope(conn, "commands") or not config.get("poll_buttons", True):
        return None
    return buttons(poll)


def on_poll_published(tenant_id=None, poll_id=None, **_):
    from hubra.core.models.poll import Poll

    poll = db.session.get(Poll, poll_id)
    if poll is None:
        return
    if poll.required_group_ids or poll.required_function_ids:
        # Only for some: their group channels, never the whole server.
        _to_groups(
            tenant_id,
            poll.required_group_ids,
            lambda c: (
                poll_embed(poll, _poll_buttons(c, poll) is not None),
                _poll_buttons(c, poll),
            ),
            f'Poll "{poll.question}"',
            live.poll_key(poll.id),
        )
        return
    _announce(
        tenant_id,
        "polls",
        "announce_polls",
        lambda c: (
            poll_embed(poll, _poll_buttons(c, poll) is not None),
            _poll_buttons(c, poll),
        ),
        f'Poll "{poll.question}"',
        live.poll_key(poll.id),
    )


def _members_only_ok(event, conn) -> bool:
    # Members-only events stay off a channel that may be public.
    return event.is_public or bool((conn.config or {}).get("members_only_events"))


def on_event_reminder(tenant_id=None, event_id=None, **_):
    from hubra.core.models.event import ClubEvent

    event = db.session.get(ClubEvent, event_id)
    if event is None:
        return

    def buttons_for(conn):
        return event_buttons(event) if apps.has_scope(conn, "commands") else None

    if _has_group_channel(tenant_id, event.group_id):
        _to_groups(
            tenant_id,
            [event.group_id],
            lambda c: (event_reminder_embed(event), buttons_for(c)),
            f'Reminder for "{event.title}"',
        )
        return

    def build(conn):
        if not _members_only_ok(event, conn):
            return None
        return event_reminder_embed(event), buttons_for(conn)

    _announce(
        tenant_id,
        "reminders",
        "announce_reminders",
        build,
        f'Reminder for "{event.title}"',
    )


def on_poll_closing(tenant_id=None, poll_id=None, **_):
    from hubra.core.models.poll import Poll

    poll = db.session.get(Poll, poll_id)
    if poll is None:
        return
    if poll.required_group_ids or poll.required_function_ids:
        _to_groups(
            tenant_id,
            poll.required_group_ids,
            lambda c: (poll_closing_embed(poll), _poll_buttons(c, poll)),
            f'Poll closing "{poll.question}"',
        )
        return
    _announce(
        tenant_id,
        "reminders",
        "announce_reminders",
        lambda c: (poll_closing_embed(poll), _poll_buttons(c, poll)),
        f'Poll closing "{poll.question}"',
    )


# ── Roles ────────────────────────────────────────────────────────────────────


def _role_connections(tenant_id):
    from .roles import managed_map

    seen, out = set(), []
    for scope in ("roles", "channels"):
        for conn in _connections(tenant_id, scope):
            if conn.id not in seen and managed_map(conn):
                seen.add(conn.id)
                out.append(conn)
    return out


def _sync_member_later(tenant_id, member_id) -> None:
    from .tasks import sync_member

    for conn in _role_connections(tenant_id):
        after_commit(sync_member.delay, conn.id, member_id)


def on_group_membership_changed(tenant_id=None, member_id=None, **_):
    if member_id is not None:
        _sync_member_later(tenant_id, member_id)


def on_account_linked(tenant_id=None, member_id=None, provider=None, **_):
    if provider == "discord" and member_id is not None:
        _sync_member_later(tenant_id, member_id)


def on_account_unlinked(tenant_id=None, provider=None, external_id=None, **_):
    from .tasks import remove_roles

    if provider != "discord" or not external_id:
        return
    for conn in _role_connections(tenant_id):
        after_commit(remove_roles.delay, conn.id, external_id)
