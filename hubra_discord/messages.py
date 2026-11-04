"""What the bot posts: embeds for events, posts and polls.

Texts are German like the rest of the bot.  Times are Discord timestamps
(``<t:…:F>``), so everyone sees them in their own time zone and language,
with "in 3 days" next to them.  Event and poll messages are kept up to date
after they are posted (live.py), so they show places taken, a cancellation,
or a poll's result.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

COLOR = 0x6D3FD8  # Hubra Violet
COLOR_DONE = 0x8A8F98  # grey: cancelled, over, closed


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


def stamp(at: datetime, style: str = "F") -> str:
    """A Discord timestamp: shown in each reader's time zone."""
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return f"<t:{int(at.timestamp())}:{style}>"


def embed(
    title: str,
    description: str | None = None,
    url: str | None = None,
    fields: list[tuple[str, str]] | None = None,
    *,
    inline: bool = True,
    footer: str | None = None,
    color: int = COLOR,
) -> dict[str, Any]:
    data: dict[str, Any] = {"title": _clip(title, 256), "color": color}
    if description:
        data["description"] = _clip(description, 2000)
    if url:
        data["url"] = url
    if fields:
        data["fields"] = [
            {"name": _clip(n, 256), "value": _clip(v, 1024), "inline": inline}
            for n, v in fields
            if v
        ][:25]
    if footer:
        data["footer"] = {"text": _clip(footer, 2048)}
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


# ── Events ───────────────────────────────────────────────────────────────────


def _when(event) -> str:
    """ "Samstag, 8. November 2026 14:00 (in 4 Tagen)" for timed events, the
    plain dates for all-day ones (a timestamp would show a midnight)."""
    if event.time and not event.all_day:
        try:
            from .scheduled import times

            start, end = times(event)
        except Exception:  # noqa: BLE001  a time we can't read: show it as typed
            start = None
        if start is not None:
            text = f"{stamp(start)} ({stamp(start, 'R')})"
            if end and end > start and end.date() == start.date():
                text = f"{stamp(start)}–{stamp(end, 't')} ({stamp(start, 'R')})"
            return text
    when = f"{event.date:%d.%m.%Y}"
    if event.end_date and event.end_date != event.date:
        when += f" – {event.end_date:%d.%m.%Y}"
    if event.time and not event.all_day:
        when += f", {event.time}"
    return when


def _places(event) -> str:
    try:
        from hubra.api.events.services import EventService

        taken = EventService.places_taken(event.id)
    except Exception:  # noqa: BLE001
        taken = len(event.attendees or [])
    if event.max_attendees:
        free = max(event.max_attendees - taken, 0)
        if free == 0:
            return f"{taken} / {event.max_attendees} · ausgebucht (Warteliste)"
        return f"{taken} / {event.max_attendees} · {free} frei"
    return f"{taken} angemeldet" if taken else "Noch niemand angemeldet"


def event_embed(event, *, state: str = "open") -> dict[str, Any]:
    """An event.  *state*: ``open``, ``over`` (it took place) or
    ``cancelled`` (it was deleted)."""
    fields = [("🗓️ Wann", _when(event)), ("📍 Wo", event.location or "")]
    if event.meeting_url:
        fields.append(("🔗 Online", event.meeting_url))
    if event.registration_deadline and state == "open":
        fields.append(("⏳ Anmeldeschluss", stamp(event.registration_deadline, "f")))
    if state == "open":
        fields.append(("👥 Plätze", _places(event)))
    footer = {
        "open": (
            "Anmelden mit den Knöpfen unten oder in der App"
            if not event.requires_approval
            else "Anmeldungen werden von den Organisatoren bestätigt"
        ),
        "over": "Dieser Anlass hat stattgefunden",
        "cancelled": "Dieser Anlass wurde abgesagt",
    }[state]
    title = f"📅 {event.title}"
    if state == "cancelled":
        title = f"❌ Abgesagt: {event.title}"
    item = embed(
        title,
        event.description,
        club_url(f"/app/operations/events?event={event.id}"),
        fields,
        footer=footer,
        color=COLOR if state == "open" else COLOR_DONE,
    )
    # The places line is long: on its own row.
    for f in item.get("fields", []):
        if f["name"].startswith(("👥", "🔗")):
            f["inline"] = False
    return item


def event_reminder_embed(event) -> dict[str, Any]:
    item = event_embed(event)
    item["title"] = _clip(f"⏰ Erinnerung: {event.title}", 256)
    return item


# ── Posts ────────────────────────────────────────────────────────────────────


def post_embed(post) -> dict[str, Any]:
    # The title and summary only: the full text stays in the club app.
    return embed(
        f"📰 {post.title}",
        post.summary,
        club_url(f"/app/operations/posts/{post.id}"),
        [("✍️ Von", post.author.name if post.author else "")],
        footer="Ganzer Beitrag in der App",
    )


# ── Polls ────────────────────────────────────────────────────────────────────

_NUMBERS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


def _options(poll) -> list:
    return sorted(poll.options, key=lambda o: o.id)


def poll_embed(poll, with_buttons: bool = False) -> dict[str, Any]:
    options = "\n".join(
        f"{_NUMBERS[i] if i < len(_NUMBERS) else '•'} {o.label}"
        for i, o in enumerate(_options(poll))
    )
    fields = []
    if poll.closes_at:
        fields.append(
            ("⏳ Schliesst", f"{stamp(poll.closes_at)} ({stamp(poll.closes_at, 'R')})")
        )
    how = (
        "Abstimmen mit den Knöpfen unten (nur du siehst deine Antwort) oder in der App."
        if with_buttons
        else "Abstimmen in der App."
    )
    return embed(
        f"🗳️ {poll.question}",
        options,
        club_url(f"/app/operations/polls?poll={poll.id}"),
        fields,
        footer=how,
    )


def poll_closing_embed(poll) -> dict[str, Any]:
    # Who still has to vote is only known in the app, so no names here.
    fields = []
    if poll.closes_at:
        fields.append(("⏳ Schliesst", stamp(poll.closes_at, "R")))
    return embed(
        f"⏳ Schliesst bald: {poll.question}",
        "Noch nicht abgestimmt? Jetzt abstimmen.",
        club_url(f"/app/operations/polls?poll={poll.id}"),
        fields,
    )


def _bar(share: float, width: int = 12) -> str:
    full = round(share * width)
    return "█" * full + "░" * (width - full)


def poll_result_embed(poll) -> dict[str, Any]:
    """A closed poll: each option with its votes, the winner first marked.
    Counts only, never who voted for what."""
    counts: dict[int, int] = {}
    for vote in poll.votes or []:
        counts[vote.option_id] = counts.get(vote.option_id, 0) + 1
    total = sum(counts.values())
    best = max(counts.values(), default=0)
    lines = []
    for o in _options(poll):
        n = counts.get(o.id, 0)
        share = n / total if total else 0
        mark = "🏆 " if best and n == best else ""
        lines.append(
            f"{mark}**{_clip(o.label, 80)}**\n`{_bar(share)}` {n} ({share:.0%})"
        )
    text = "\n".join(lines) or "–"
    return embed(
        f"✅ Beendet: {poll.question}",
        text,
        club_url(f"/app/operations/polls?poll={poll.id}"),
        footer=f"{total} Stimme{'n' if total != 1 else ''} · Umfrage geschlossen",
        color=COLOR_DONE,
    )
