"""Moderation: reports and new people (``moderate`` scope).

Both go to the moderation channel (``mod_channel_id``, else the bot log
channel), which should be for admins only (the layout's #hubra-log is).

* **Melden** (``reports``): right-click a message › Apps › "Melden". The
  reporter writes why (a form only they see); the admins get a card with
  the message, its author, the reason and a link, and the buttons
  "Nachricht löschen" and "Erledigt". Anyone on the server can report.
* **Neue Leute** (``guest_queue``): someone joins who isn't a linked member.
  The admins get a card with "Als Gast zulassen" (gives the guest role,
  default "Gast", made when first needed) and "Entfernen" (kicks). Members
  who link Discord get their roles as before.

The card buttons check the clicker's rights on Discord: Manage Messages for
reports, Kick Members / Manage Roles for new people (or Administrator /
Manage Server). After a click the card shows who did what, without buttons.
"""

from __future__ import annotations

from hubra.core import apps

from . import api, botlog
from .api import DiscordError

REPORT_COMMAND = {
    "name": "Melden",
    "name_localizations": {"en-GB": "Report", "en-US": "Report"},
    "type": 3,  # on a message: Apps › Melden
    "contexts": [0],
}
DEFAULT_GUEST = "Gast"
MAX_REASON = 500
RED = 0xD9480F
GREY = 0x868E96
GREEN = 0x2F9E44


def channel(conn) -> str | None:
    config = conn.config or {}
    return config.get("mod_channel_id") or config.get("log_channel_id")


def _perms(interaction: dict) -> int:
    try:
        return int((interaction.get("member") or {}).get("permissions") or 0)
    except ValueError:
        return 0


def _may(interaction: dict, bits: int) -> bool:
    have = _perms(interaction)
    return bool(have & (api.ADMINISTRATOR | api.MANAGE_GUILD | bits))


def _who(interaction: dict) -> str:
    user = (interaction.get("member") or {}).get("user") or {}
    return user.get("global_name") or user.get("username") or "?"


def _ephemeral(text: str) -> dict:
    from .commands import reply

    return reply(text)


def _off(conn, key: str) -> str | None:
    if not apps.has_scope(conn, "moderate"):
        return "Moderation ist für diesen Verein ausgeschaltet."
    if not (conn.config or {}).get(key, key == "reports"):
        return "Das ist für diesen Verein ausgeschaltet."
    if not channel(conn):
        return "Der Verein hat keinen Moderationskanal gewählt."
    return None


# ── Reports ──────────────────────────────────────────────────────────────────


def report_form(conn, interaction: dict) -> dict:
    """The message command: ask why (a modal)."""
    off = _off(conn, "reports")
    if off:
        return _ephemeral(off)
    data = interaction.get("data") or {}
    message_id = str(data.get("target_id") or "")
    channel_id = str(interaction.get("channel_id") or "")
    if not message_id.isdigit() or not channel_id.isdigit():
        return _ephemeral("Diese Nachricht kann Hubra nicht melden.")
    return {
        "type": 9,
        "data": {
            "custom_id": f"mrep:{channel_id}:{message_id}",
            "title": "Nachricht melden",
            "components": [
                {
                    "type": 1,
                    "components": [
                        {
                            "type": 4,
                            "custom_id": "reason",
                            "label": "Was ist das Problem?",
                            "style": 2,
                            "min_length": 3,
                            "max_length": MAX_REASON,
                            "required": True,
                        }
                    ],
                }
            ],
        },
    }


def _field(interaction: dict, custom_id: str) -> str:
    for row in (interaction.get("data") or {}).get("components") or []:
        for item in row.get("components") or []:
            if item.get("custom_id") == custom_id:
                return str(item.get("value") or "")
    return ""


def report_sent(conn, interaction: dict) -> dict:
    """The filled-in form: the card for the admins."""
    off = _off(conn, "reports")
    if off:
        return _ephemeral(off)
    parts = str((interaction.get("data") or {}).get("custom_id", "")).split(":")
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        return _ephemeral("Diese Meldung ging nicht durch.")
    _, channel_id, message_id = parts
    reason = " ".join(_field(interaction, "reason").split())[:MAX_REASON]
    guild = str(conn.external_id)
    link = f"https://discord.com/channels/{guild}/{channel_id}/{message_id}"
    try:
        found = api.message(channel_id, message_id) or {}
    except DiscordError:
        found = {}
    author = (found.get("author") or {}).get("id")
    text = str(found.get("content") or "").strip() or "(kein Text, vielleicht ein Bild)"
    card = {
        "embeds": [
            {
                "title": "🚩 Gemeldete Nachricht",
                "url": link,
                "description": text[:1500],
                "color": RED,
                "fields": [
                    {
                        "name": "Von",
                        "value": f"<@{author}>" if author else "?",
                        "inline": True,
                    },
                    {"name": "In", "value": f"<#{channel_id}>", "inline": True},
                    {
                        "name": "Gemeldet von",
                        "value": f"<@{(interaction.get('member') or {}).get('user', {}).get('id', '')}>",
                        "inline": True,
                    },
                    {"name": "Grund", "value": reason or "–", "inline": False},
                ],
            }
        ],
        "components": [
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 4,
                        "label": "Nachricht löschen",
                        "custom_id": f"mdel:{channel_id}:{message_id}",
                    },
                    {"type": 2, "style": 2, "label": "Erledigt", "custom_id": "mdone"},
                    {"type": 2, "style": 5, "label": "Ansehen", "url": link},
                ],
            }
        ],
        "allowed_mentions": {"parse": []},
    }
    try:
        api.send(channel(conn), card)
    except DiscordError as exc:
        botlog.log(conn, "report", False, f"Meldung nicht zugestellt: {exc}")
        return _ephemeral(
            "Die Meldung ging nicht durch; sag es bitte direkt einem Admin."
        )
    botlog.log(conn, "report", True, f"Meldung von {_who(interaction)}: {reason[:200]}")
    return _ephemeral("Danke. Die Admins schauen sich die Nachricht an.")


def _closed(interaction: dict, line: str, color: int) -> dict:
    """Answer a card's button by editing the card: who did what, no buttons."""
    message = interaction.get("message") or {}
    embeds = [dict(e) for e in message.get("embeds") or [{}]]
    first = embeds[0]
    first["color"] = color
    first["fields"] = [
        *(first.get("fields") or []),
        {"name": "✔", "value": line[:1000]},
    ][:25]
    embeds[0] = first
    return {
        "type": 7,  # update the message the button is on
        "data": {"embeds": embeds, "components": [], "allowed_mentions": {"parse": []}},
    }


def report_button(conn, interaction: dict, custom_id: str) -> dict:
    if not apps.has_scope(conn, "moderate"):
        return _ephemeral("Moderation ist für diesen Verein ausgeschaltet.")
    if not _may(interaction, api.MANAGE_MESSAGES):
        return _ephemeral(
            "Dafür brauchst du «Nachrichten verwalten» auf diesem Server."
        )
    who = _who(interaction)
    if custom_id == "mdone":
        return _closed(interaction, f"Erledigt von {who}", GREY)
    parts = custom_id.split(":")
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        return _ephemeral("Diese Schaltfläche gilt nicht mehr.")
    try:
        gone = api.delete_message(
            parts[1], parts[2], f"Hubra: gemeldet, gelöscht von {who}"
        )
    except DiscordError as exc:
        return _ephemeral(f"Löschen ging nicht: {exc}")
    botlog.log(conn, "report", True, f"Gemeldete Nachricht gelöscht von {who}")
    line = f"Gelöscht von {who}" if gone else f"War schon weg ({who})"
    return _closed(interaction, line, GREEN)


# ── New people ───────────────────────────────────────────────────────────────


def newcomer_card(user_id: str) -> dict:
    return {
        "embeds": [
            {
                "title": "👋 Neu auf dem Server",
                "description": (
                    f"<@{user_id}> ist kein verknüpftes Mitglied. Zulassen gibt "
                    "die Gast-Rolle; Mitglieder verknüpfen sich mit /verbinden."
                ),
                "color": 0x6D3FD8,
            }
        ],
        "components": [
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 3,
                        "label": "Als Gast zulassen",
                        "custom_id": f"gok:{user_id}",
                    },
                    {
                        "type": 2,
                        "style": 4,
                        "label": "Entfernen",
                        "custom_id": f"gkick:{user_id}",
                    },
                ],
            }
        ],
        "allowed_mentions": {"parse": []},
    }


def newcomer(conn, user_id: str) -> str:
    """From the gateway: someone who isn't a member joined. Raises
    ``DiscordError``."""
    if _off(conn, "guest_queue") or not apps.has_scope(conn, "commands"):
        return ""
    api.send(channel(conn), newcomer_card(user_id))
    return "newcomer card for the admins"


def guest_role(conn) -> str:
    """The guest role's id, made when first needed."""
    config = conn.config or {}
    name = (str(config.get("guest_role_name") or "").strip() or DEFAULT_GUEST)[:100]
    stored = dict(config.get("guest_role") or {})
    server = {r["id"]: r for r in api.roles(conn.external_id)}
    if stored.get("role_id") in server:
        if server[stored["role_id"]].get("name") != name:
            api.edit_role(
                conn.external_id, stored["role_id"], {"name": name}, "Hubra: guest role"
            )
            stored["name"] = name
        role_id = stored["role_id"]
    else:
        same = next(
            (
                r
                for r in server.values()
                if r.get("name") == name and not r.get("managed")
            ),
            None,
        )
        role = same or api.create_role(conn.external_id, name, "Hubra: guest role")
        role_id = str(role["id"])
    new = dict(config)
    new["guest_role"] = {"role_id": role_id, "name": name}
    conn.config = new
    return role_id


def newcomer_button(conn, interaction: dict, custom_id: str) -> dict:
    from hubra.core.extensions import db

    if not apps.has_scope(conn, "moderate"):
        return _ephemeral("Moderation ist für diesen Verein ausgeschaltet.")
    action, _, user_id = custom_id.partition(":")
    if not user_id.isdigit():
        return _ephemeral("Diese Schaltfläche gilt nicht mehr.")
    who = _who(interaction)
    try:
        if action == "gok":
            if not _may(interaction, api.MANAGE_ROLES):
                return _ephemeral(
                    "Dafür brauchst du «Rollen verwalten» auf diesem Server."
                )
            role = guest_role(conn)
            api.add_role(
                conn.external_id, user_id, role, f"Hubra: als Gast zugelassen von {who}"
            )
            db.session.commit()
            botlog.log(
                conn, "guests", True, f"<@{user_id}> als Gast zugelassen von {who}"
            )
            return _closed(interaction, f"Als Gast zugelassen von {who}", GREEN)
        if not _may(interaction, api.KICK_MEMBERS):
            return _ephemeral(
                "Dafür brauchst du «Mitglieder kicken» auf diesem Server."
            )
        api.kick(conn.external_id, user_id, f"Hubra: entfernt von {who}")
        botlog.log(conn, "guests", True, f"<@{user_id}> entfernt von {who}")
        return _closed(interaction, f"Entfernt von {who}", GREY)
    except DiscordError as exc:
        if exc.status == 404:
            return _closed(interaction, "Nicht mehr auf dem Server", GREY)
        return _ephemeral(f"Das ging nicht: {exc}")
