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

from .commands import event_buttons
from .messages import event_embed, payload, poll_embed, post_embed

_log = logging.getLogger(__name__)


# ── After the commit ─────────────────────────────────────────────────────────


def after_commit(fn, *args) -> None:
    """Call ``fn(*args)`` once the current transaction is committed."""
    session = db.session()
    if not session.info.get("discord_listening"):
        sa_event.listen(session, "after_commit", _run_pending)
        sa_event.listen(session, "after_soft_rollback", _drop_pending)
        session.info["discord_listening"] = True
    session.info.setdefault("discord_pending", []).append((fn, args))


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


def _announce(tenant_id, toggle: str, build) -> None:
    from .tasks import send_announcement

    for conn in _connections(tenant_id, "announce"):
        config = conn.config or {}
        if not config.get("channel_id") or not config.get(toggle):
            continue
        built = build(conn)
        if built is None:
            continue
        item, components = built if isinstance(built, tuple) else (built, None)
        body = payload(config.get("mention"), [item], components)
        after_commit(send_announcement.delay, conn.id, body)


def on_event_published(tenant_id=None, event_id=None, **_):
    from hubra.core.models.event import ClubEvent

    event = db.session.get(ClubEvent, event_id)
    if event is None:
        return

    def build(conn):
        # Members-only events stay off a channel that may be public.
        if not event.is_public and not (conn.config or {}).get("members_only_events"):
            return None
        buttons = event_buttons(event) if apps.has_scope(conn, "commands") else None
        return event_embed(event), buttons

    _announce(tenant_id, "announce_events", build)


def on_post_published(tenant_id=None, post_id=None, **_):
    from hubra.core.models.post import Post

    post = db.session.get(Post, post_id)
    if post is not None:
        _announce(tenant_id, "announce_posts", lambda _conn: post_embed(post))


def on_poll_published(tenant_id=None, poll_id=None, **_):
    from hubra.core.models.poll import Poll

    poll = db.session.get(Poll, poll_id)
    if poll is None or poll.required_group_ids or poll.required_function_ids:
        return  # a poll for some groups only isn't for the whole server
    _announce(tenant_id, "announce_polls", lambda _conn: poll_embed(poll))


# ── Roles ────────────────────────────────────────────────────────────────────


def _sync_member_later(tenant_id, member_id) -> None:
    from .tasks import sync_member

    for conn in _connections(tenant_id, "roles"):
        if (conn.config or {}).get("role_map"):
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
    for conn in _connections(tenant_id, "roles"):
        if (conn.config or {}).get("role_map"):
            after_commit(remove_roles.delay, conn.id, external_id)
