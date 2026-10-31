"""What the bot posts: embeds for events, posts and polls."""

from __future__ import annotations

from typing import Any

COLOR = 0x6D3FD8  # Hubra Violet


def club_url(path: str) -> str | None:
    """A link into the club app, with the club in it (/<club>/app/…), so a
    member of several clubs lands in the right one."""
    from hubra.core.services.email import _get_base_url
    from hubra.core.tenant.context import current_tenant

    base = _get_base_url()
    if not base:
        return None
    tenant = current_tenant()
    prefix = f"/{tenant.slug}" if tenant is not None else ""
    return f"{base.rstrip('/')}{prefix}{path}"


def _clip(text: str | None, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def embed(
    title: str,
    description: str | None = None,
    url: str | None = None,
    fields: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {"title": _clip(title, 256), "color": COLOR}
    if description:
        data["description"] = _clip(description, 2000)
    if url:
        data["url"] = url
    if fields:
        data["fields"] = [
            {"name": _clip(n, 256), "value": _clip(v, 1024), "inline": True}
            for n, v in fields
            if v
        ][:25]
    return data


def payload(mention: str | None, embeds: list[dict], components=None) -> dict[str, Any]:
    """Only the configured mention may ping anyone; text inside an event or
    post never does."""
    data: dict[str, Any] = {"embeds": embeds, "allowed_mentions": {"parse": []}}
    if mention in ("everyone", "here"):
        data["content"] = f"@{mention}"
        data["allowed_mentions"] = {"parse": ["everyone"]}
    elif mention and mention.startswith("role:"):
        role = mention[5:]
        data["content"] = f"<@&{role}>"
        data["allowed_mentions"] = {"parse": [], "roles": [role]}
    if components:
        data["components"] = components
    return data


def event_embed(event) -> dict[str, Any]:
    when = event.date.strftime("%d.%m.%Y")
    if event.end_date and event.end_date != event.date:
        when += f" – {event.end_date:%d.%m.%Y}"
    if event.time and not event.all_day:
        when += f", {event.time}"
    return embed(
        f"📅 {event.title}",
        event.description,
        club_url("/app/operations/events"),
        [("When", when), ("Where", event.location or "")],
    )


def post_embed(post) -> dict[str, Any]:
    # The title and summary only: the full text stays in the club app.
    return embed(
        f"📰 {post.title}",
        post.summary,
        club_url(f"/app/operations/posts/{post.id}"),
        [("By", post.author.name if post.author else "")],
    )


def poll_embed(poll) -> dict[str, Any]:
    options = "\n".join(f"• {o.label}" for o in poll.options)
    fields = []
    if poll.closes_at:
        fields.append(("Closes", poll.closes_at.strftime("%d.%m.%Y %H:%M")))
    return embed(
        f"🗳️ {poll.question}",
        f"{options}\n\nVote in the member portal.",
        club_url("/app/operations/polls"),
        fields,
    )
