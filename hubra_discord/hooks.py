"""Event handlers: turn suite events into Discord announcements."""

from __future__ import annotations

import logging

from sqlalchemy import event as sa_event

from hubra.core.extensions import db
from hubra.core.modules import get_config

from .webhook import embed, is_webhook_url, payload

_log = logging.getLogger(__name__)


def _app_url(path: str) -> str | None:
    from hubra.core.services.email import _get_base_url

    base = _get_base_url()
    return f"{base.rstrip('/')}{path}" if base else None


def _queue(tenant_id, setting: str, item_embed: dict) -> None:
    config = get_config("discord", tenant_id=tenant_id)
    if not config.get(setting):
        return
    url = config.get("webhook_url")
    if not is_webhook_url(url):
        _log.warning("discord: webhook_url is missing or not a Discord webhook")
        return
    _after_commit(url, payload(config.get("mention") or None, [item_embed]))


def _after_commit(url: str, body: dict) -> None:
    """Send once the request's transaction is committed, so a change that is
    rolled back afterwards is never announced."""
    session = db.session()
    if not session.info.get("discord_listening"):
        sa_event.listen(session, "after_commit", _send_pending)
        sa_event.listen(session, "after_soft_rollback", _drop_pending)
        session.info["discord_listening"] = True
    session.info.setdefault("discord_pending", []).append((url, body))


def _send_pending(session) -> None:
    pending = session.info.pop("discord_pending", None)
    if not pending:
        return
    from .tasks import post_to_discord

    for url, body in pending:
        try:
            post_to_discord.delay(url, body)
        except Exception:
            # The commit is done; a broker outage must not turn it into a 500.
            _log.exception("discord: could not queue the announcement")


def _drop_pending(session, previous_transaction) -> None:
    if not previous_transaction.nested:  # a savepoint leaves the rest alive
        session.info.pop("discord_pending", None)


def on_event_published(tenant_id=None, event_id=None, **_):
    from hubra.core.models.event import ClubEvent

    event = db.session.get(ClubEvent, event_id)
    if event is None:
        return
    when = event.date.strftime("%d.%m.%Y")
    if event.end_date and event.end_date != event.date:
        when += f" – {event.end_date:%d.%m.%Y}"
    if event.time and not event.all_day:
        when += f", {event.time}"
    _queue(
        tenant_id,
        "announce_events",
        embed(
            f"📅 {event.title}",
            event.description,
            _app_url(f"/app/operations/events?event={event.id}"),
            [("When", when), ("Where", event.location or "")],
        ),
    )


def on_post_published(tenant_id=None, post_id=None, **_):
    from hubra.core.models.post import Post

    post = db.session.get(Post, post_id)
    if post is None:
        return
    _queue(
        tenant_id,
        "announce_posts",
        embed(
            f"📰 {post.title}",
            post.summary or post.body,
            _app_url(f"/app/operations/posts/{post.id}"),
            [("By", post.author.name if post.author else "")],
        ),
    )


def on_poll_published(tenant_id=None, poll_id=None, **_):
    from hubra.core.models.poll import Poll

    poll = db.session.get(Poll, poll_id)
    if poll is None:
        return
    options = "\n".join(f"• {o.label}" for o in poll.options)
    fields = []
    if poll.closes_at:
        fields.append(("Closes", poll.closes_at.strftime("%d.%m.%Y %H:%M")))
    _queue(
        tenant_id,
        "announce_polls",
        embed(
            f"🗳️ {poll.question}",
            f"{options}\n\nVote in the member portal.",
            _app_url("/app/operations/polls"),
            fields,
        ),
    )
