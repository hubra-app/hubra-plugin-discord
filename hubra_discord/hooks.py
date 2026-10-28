"""Event handlers: turn suite events into Discord announcements."""

from __future__ import annotations

import logging

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
    from .tasks import post_to_discord

    post_to_discord.delay(url, payload(config.get("mention") or None, [item_embed]))


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
