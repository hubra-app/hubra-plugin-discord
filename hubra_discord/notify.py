"""Direct messages from the bot: notifications and the welcome message.

Notifications: a member turns Discord on per topic (Mein Konto ›
Benachrichtigungen); Hubra core hands each such notification to
``deliver`` (the ``deliver_notification`` hook), and the bot sends it as a
direct message, with the club's colour and name.

Welcome: when someone joins the club's server (seen by the gateway process,
gateway.py), the bot greets them by direct message if the club turned it on
and they haven't linked a member account yet, and points them to linking.
Someone who already linked gets their roles right away instead.

Discord only delivers a bot's direct message to someone who shares a server
with the bot and allows direct messages from that server's members.
"""

from __future__ import annotations

from hubra.core import apps

from . import api, botlog
from .api import DiscordError
from .messages import COLOR

# Discord's code for "Cannot send messages to this user".
CANNOT_DM = 50007
MAX_WELCOME = 1500
DEFAULT_WELCOME = (
    "Willkommen auf dem Discord-Server von {club}! 👋\n\n"
    "Bist du Mitglied? Verknüpfe dein Discord-Konto mit Hubra, dann bekommst "
    "du deine Rollen und Kanäle automatisch: {link}"
)


def _branded(body: dict) -> dict:
    from hubra.core.tenant.context import current_tenant

    from .profile import brand

    # Direct messages come from the bot as "Hubra": always name the club.
    return brand(body, current_tenant(), {"club_embeds": True})


# A sign per topic, so a direct message says at a glance what it is about.
TOPIC_ICONS = {
    "bookings": "✅",
    "waitlist": "🎟️",
    "event_reminders": "⏰",
    "poll_reminders": "🗳️",
    "my_events": "📅",
    "requests": "📨",
    "payments": "💳",
    "membership": "🔁",
    "qualifications": "🎓",
    "digest": "🗓️",
}
# Warnings stand out: overdue invoices, a qualification that ends.
WARN_COLOR = 0xE8A33D
MAX_FIELDS = 10


def notification_embed(payload: dict) -> dict:
    """The embed for one notification payload (``hubra.core.notifications.
    channels``): title with the topic's sign, the text, the link, and the
    ``fields`` a payload may carry (the weekly digest)."""
    from .messages import COLOR, club_url

    link = club_url(payload.get("link") or "/app/dashboard")
    title = payload.get("title") or ""
    icon = TOPIC_ICONS.get(payload.get("topic") or "")
    if icon and not title.startswith(icon):
        title = f"{icon} {title}"
    item = {
        "title": title[:256],
        "color": WARN_COLOR if payload.get("kind") == "warning" else COLOR,
    }
    if payload.get("msg"):
        item["description"] = payload["msg"][:2000]
    if link:
        item["url"] = link
    fields = []
    for field in (payload.get("fields") or [])[:MAX_FIELDS]:
        if not isinstance(field, dict):
            continue
        name, value = str(field.get("name") or ""), str(field.get("value") or "")
        if name and value:
            fields.append({"name": name[:256], "value": value[:1024]})
    if fields:
        item["fields"] = fields
    return item


def deliver(conn, external_id: str, payload: dict) -> bool:
    """The ``deliver_notification`` hook."""
    if not apps.has_scope(conn, "notify"):
        return False
    item = notification_embed(payload)
    body = _branded({"embeds": [item], "allowed_mentions": {"parse": []}})
    if payload.get("kind") == "warning":
        # The club's name and logo, but a warning keeps its orange.
        body["embeds"][0]["color"] = WARN_COLOR
    try:
        api.dm(external_id, body)
    except DiscordError as exc:
        if exc.status == 403:
            # Their privacy settings: not the club's problem, but say so.
            botlog.skipped(
                conn,
                f'Direct message "{item["title"]}"',
                "the person doesn't accept direct messages from this server "
                f"(Discord {exc.code or 403})",
            )
            return False
        from .diagnose import describe

        raise apps.AppError(describe(exc, doing="Direct message")) from exc
    botlog.log(conn, "dm", True, f'Notification "{item["title"]}"')
    return True


def welcome_text(conn) -> str:
    from hubra.core.tenant.context import current_tenant

    from .commands import _link_hint
    from .profile import club_name

    text = ((conn.config or {}).get("welcome_text") or "").strip() or DEFAULT_WELCOME
    hint = _link_hint().split(": ", 1)[-1]
    return text.replace("{club}", club_name(current_tenant()) or "Hubra").replace(
        "{link}", hint
    )[:MAX_WELCOME]


def member_joined(conn, user_id: str) -> str:
    """Someone joined the server. Returns what was done (for the log)."""
    from .commands import club_member
    from .roles import managed_map, sync_member

    member = club_member(user_id)
    if member is not None:
        if managed_map(conn):
            return sync_member(conn, member) or "joined (roles already right)"
        return "joined (member)"
    from .mod import newcomer

    try:
        queued = newcomer(conn, user_id)
    except DiscordError as exc:  # the welcome still goes out
        botlog.log(conn, "guests", False, f"Karte für neue Person: {exc}")
        queued = ""
    if not apps.has_scope(conn, "notify") or not (conn.config or {}).get("welcome_dm"):
        return queued
    # An embed, so the welcome carries the club's name, logo and colour like
    # every other direct message (the sender itself is always the bot).
    body = _branded(
        {
            "embeds": [{"description": welcome_text(conn), "color": COLOR}],
            "allowed_mentions": {"parse": []},
        }
    )
    try:
        api.dm(user_id, body)
    except DiscordError as exc:
        if exc.status == 403:  # they don't take direct messages
            return ", ".join(
                x for x in (queued, "welcome not sent (direct messages closed)") if x
            )
        raise
    return ", ".join(x for x in (queued, "welcome sent") if x)
