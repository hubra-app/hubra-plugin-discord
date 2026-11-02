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

from . import api
from .api import DiscordError

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


def deliver(conn, external_id: str, payload: dict) -> bool:
    """The ``deliver_notification`` hook."""
    from .messages import COLOR, club_url

    if not apps.has_scope(conn, "notify"):
        return False
    link = club_url(payload.get("link") or "/app/dashboard")
    item = {"title": (payload.get("title") or "")[:256], "color": COLOR}
    if payload.get("msg"):
        item["description"] = payload["msg"][:2000]
    if link:
        item["url"] = link
    try:
        api.dm(
            external_id, _branded({"embeds": [item], "allowed_mentions": {"parse": []}})
        )
    except DiscordError as exc:
        if exc.status == 403:
            # Their privacy settings: not the club's problem, nothing to record.
            return False
        raise apps.AppError(f"Direct message failed: {exc}") from exc
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
    if not apps.has_scope(conn, "notify") or not (conn.config or {}).get("welcome_dm"):
        return ""
    body = {
        "content": welcome_text(conn),
        "allowed_mentions": {"parse": []},
    }
    try:
        api.dm(user_id, body)
    except DiscordError as exc:
        if exc.status == 403:
            return ""  # they don't take direct messages
        raise
    return "welcome sent"
