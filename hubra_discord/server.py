"""The club's Discord server from Hubra: channels and the bot's messages.

Settings › Verbundene Apps › Discord › Kanäle lists the server's channels
with what Hubra uses each one for, and (with the ``channels`` scope) lets a
club admin make, rename, move and delete channels through the bot, without
opening Discord.  The bot only deletes channels it made itself (kept in
``config["made_channels"]``, plus the group channels), never one people
made, and never one Hubra still posts in.

Nachrichten lists the event and poll messages the bot keeps up to date
(live.py), with a link to each on Discord.
"""

from __future__ import annotations

import logging

from hubra.core import apps
from hubra.core.extensions import db

from . import api, botlog
from .api import DiscordError

_log = logging.getLogger(__name__)

MADE_KEY = "made_channels"
MAX_MADE = 200
TYPES = {
    0: "text",
    2: "voice",
    4: "category",
    5: "announcement",
    13: "stage",
    15: "forum",
}
KIND_TO_TYPE = {"text": 0, "voice": 2, "category": 4}
USE_LABELS = {
    "main": "Hauptkanal",
    "events": "Anlässe",
    "posts": "Beiträge",
    "polls": "Umfragen",
    "reminders": "Erinnerungen",
    "log": "Bot-Protokoll",
    "group": "Gruppenkanal",
}
MAX_NAME = 100
MAX_TOPIC = 1024


class ServerError(Exception):
    """Something a club admin can fix, in their words."""


def _made(config: dict) -> set[str]:
    made = {str(c) for c in config.get(MADE_KEY) or []}
    made |= {str(g.get("channel_id")) for g in config.get("group_channels") or []}
    made.discard("None")
    return made


def uses(config: dict) -> dict[str, list[str]]:
    """Channel id → what Hubra uses it for (German labels)."""
    from .app import used_channels

    out = {
        cid: [USE_LABELS.get(u, u) for u in what]
        for cid, what in used_channels(config).items()
    }
    for g in config.get("group_channels") or []:
        if g.get("channel_id"):
            out.setdefault(str(g["channel_id"]), []).append(
                f"Gruppe {g.get('name') or g.get('group_id')}"
            )
    return out


def _bits(guild_id: str) -> tuple[int, set[str], dict[str, int]]:
    """The bot's server-wide permissions, its roles and every role's bits:
    read once, then each channel is worked out offline."""
    roles = {r["id"]: int(r.get("permissions") or 0) for r in api.roles(guild_id)}
    me = api.member(guild_id, api.config("client_id")) or {}
    mine = set(me.get("roles", []))
    bits = roles.get(str(guild_id), 0)
    for r in mine:
        bits |= roles.get(r, 0)
    return bits, mine, roles


def _in_channel(channel: dict, guild_id: str, base: int, mine: set[str]) -> int:
    if base & api.ADMINISTRATOR:
        return (1 << 53) - 1
    overwrites = {str(o["id"]): o for o in channel.get("permission_overwrites") or []}
    bits = base
    everyone = overwrites.get(str(guild_id))
    if everyone:
        bits = (bits & ~int(everyone.get("deny") or 0)) | int(
            everyone.get("allow") or 0
        )
    allow = deny = 0
    for r in mine:
        o = overwrites.get(r)
        if o:
            allow |= int(o.get("allow") or 0)
            deny |= int(o.get("deny") or 0)
    bits = (bits & ~deny) | allow
    me = overwrites.get(str(api.config("client_id")))
    if me:
        bits = (bits & ~int(me.get("deny") or 0)) | int(me.get("allow") or 0)
    return bits


def _private(channel: dict, guild_id: str) -> bool:
    for o in channel.get("permission_overwrites") or []:
        if str(o["id"]) == str(guild_id) and int(o.get("deny") or 0) & api.VIEW_CHANNEL:
            return True
    return False


def overview(conn) -> dict:
    """The server's channels in Discord's order, grouped by category."""
    guild = conn.external_id
    config = conn.config or {}
    try:
        channels = api.channels(guild)
        base, mine, _ = _bits(guild)
    except DiscordError as exc:
        raise ServerError(f"Discord hat die Kanäle nicht geschickt: {exc}") from None
    used = uses(config)
    made = _made(config)
    send = api.VIEW_CHANNEL | api.SEND_MESSAGES | api.EMBED_LINKS
    cats = {str(c["id"]): c for c in channels if c.get("type") == 4}

    def order(c):
        parent = cats.get(str(c.get("parent_id") or ""))
        if c.get("type") == 4:
            return (c.get("position", 0), -1, 0)
        top = parent.get("position", 0) if parent else -1
        return (top, 1 if c.get("type") in (2, 13) else 0, c.get("position", 0))

    out = []
    for c in sorted(channels, key=order):
        kind = TYPES.get(c.get("type"), "other")
        cid = str(c["id"])
        bits = _in_channel(c, guild, base, mine)
        out.append(
            {
                "id": cid,
                "name": c.get("name", ""),
                "kind": kind,
                "parentId": str(c["parent_id"]) if c.get("parent_id") else None,
                "topic": c.get("topic") or "",
                "private": _private(c, guild),
                "uses": used.get(cid, []),
                "madeByHubra": cid in made,
                "botCanPost": kind in ("text", "announcement") and bits & send == send,
                "botCanSee": bool(bits & api.VIEW_CHANNEL),
                "url": f"https://discord.com/channels/{guild}/{cid}",
            }
        )
    return {
        "channels": out,
        "canManage": apps.has_scope(conn, "channels")
        and bool(base & (api.MANAGE_CHANNELS | api.ADMINISTRATOR)),
        "hasScope": apps.has_scope(conn, "channels"),
    }


def _need_scope(conn) -> None:
    if not apps.has_scope(conn, "channels"):
        raise ServerError(
            "Kanäle verwalten ist für diese Verbindung nicht freigegeben "
            "(Bereich «Kanäle» unter Darf)."
        )


def _clean_name(name: str, kind: str) -> str:
    name = " ".join(str(name or "").split())[:MAX_NAME]
    if kind == "text":
        name = name.lower().replace(" ", "-")
    if not name:
        raise ServerError("Gib dem Kanal einen Namen.")
    return name


def _channel_of(conn, channel_id: str) -> dict:
    if not str(channel_id).isdigit():
        raise ServerError("Unbekannter Kanal.")
    try:
        found = api.channel(channel_id)
    except DiscordError as exc:
        raise ServerError(str(exc)) from None
    if found is None or str(found.get("guild_id")) != str(conn.external_id):
        raise ServerError("Diesen Kanal gibt es auf eurem Server nicht.")
    return found


def _error(exc: DiscordError, what: str) -> ServerError:
    if exc.status == 403:
        return ServerError(
            f"{what}: Der Hubra-Rolle fehlt «Kanäle verwalten» (bei privaten "
            "Kanälen auch «Rollen verwalten»). Füge den Bot erneut hinzu oder "
            "gib der Rolle die Rechte in den Servereinstellungen."
        )
    return ServerError(f"{what}: {exc}")


def create(
    conn,
    name: str,
    kind: str = "text",
    *,
    parent_id: str | None = None,
    topic: str = "",
    private: bool = False,
    role_ids: list[str] | None = None,
    who: str = "",
) -> dict:
    """A new channel or category, made by the bot."""
    _need_scope(conn)
    if kind not in KIND_TO_TYPE:
        raise ServerError("Unbekannte Kanalart.")
    body: dict = {"name": _clean_name(name, kind), "type": KIND_TO_TYPE[kind]}
    if parent_id and kind != "category":
        parent = _channel_of(conn, parent_id)
        if parent.get("type") != 4:
            raise ServerError("Das ist keine Kategorie.")
        body["parent_id"] = str(parent_id)
    if topic and kind == "text":
        body["topic"] = str(topic).strip()[:MAX_TOPIC]
    if private:
        see = api.VIEW_CHANNEL | api.SEND_MESSAGES | api.EMBED_LINKS
        if kind == "voice":
            see |= 1 << 20  # Connect
        overwrites = [
            {
                "id": str(conn.external_id),
                "type": 0,
                "deny": str(api.VIEW_CHANNEL),
                "allow": "0",
            },
            {
                "id": str(api.config("client_id")),
                "type": 1,
                "allow": str(see),
                "deny": "0",
            },
        ]
        for rid in role_ids or []:
            if str(rid).isdigit():
                overwrites.append(
                    {"id": str(rid), "type": 0, "allow": str(see), "deny": "0"}
                )
        body["permission_overwrites"] = overwrites
    try:
        made = api.create_channel(
            conn.external_id, body, f"Hubra: angelegt von {who or 'einem Admin'}"
        )
    except DiscordError as exc:
        botlog.log(conn, "channels", False, f"#{body['name']} anlegen: {exc}")
        raise _error(exc, "Kanal nicht angelegt") from None
    config = dict(conn.config or {})
    config[MADE_KEY] = ([*(config.get(MADE_KEY) or []), str(made["id"])])[-MAX_MADE:]
    conn.config = config
    botlog.log(
        conn, "channels", True, f"#{made.get('name', body['name'])} angelegt ({who})"
    )
    return {"id": str(made["id"]), "name": made.get("name", body["name"])}


def edit(conn, channel_id: str, changes: dict, who: str = "") -> dict:
    """Rename a channel, change its topic or move it to another category."""
    _need_scope(conn)
    found = _channel_of(conn, channel_id)
    kind = TYPES.get(found.get("type"), "other")
    body: dict = {}
    if "name" in changes:
        body["name"] = _clean_name(changes["name"], kind)
    if "topic" in changes and kind in ("text", "announcement", "forum"):
        body["topic"] = str(changes.get("topic") or "").strip()[:MAX_TOPIC]
    if "parentId" in changes and kind != "category":
        parent = changes.get("parentId")
        if parent:
            if _channel_of(conn, parent).get("type") != 4:
                raise ServerError("Das ist keine Kategorie.")
            body["parent_id"] = str(parent)
        else:
            body["parent_id"] = None
    if not body:
        raise ServerError("Nichts zu ändern.")
    try:
        done = api.edit_channel(
            channel_id, body, f"Hubra: geändert von {who or 'einem Admin'}"
        )
    except DiscordError as exc:
        botlog.log(conn, "channels", False, f"#{found.get('name')} ändern: {exc}")
        raise _error(exc, "Kanal nicht geändert") from None
    botlog.log(
        conn,
        "channels",
        True,
        f"#{found.get('name')} geändert: {', '.join(sorted(body))} ({who})",
    )
    return {"id": str(channel_id), "name": (done or {}).get("name", body.get("name"))}


def delete(conn, channel_id: str, who: str = "") -> None:
    """Delete a channel the bot made, once Hubra no longer posts there."""
    _need_scope(conn)
    config = dict(conn.config or {})
    cid = str(channel_id)
    if cid not in _made(config):
        raise ServerError(
            "Hubra löscht nur Kanäle, die der Bot selbst angelegt hat. Diesen "
            "löscht ihr auf Discord."
        )
    still = uses(config).get(cid)
    if still and any(not u.startswith("Gruppe ") for u in still):
        raise ServerError(
            f"Hubra schreibt noch in diesen Kanal ({', '.join(still)}). Wähle "
            "zuerst einen anderen."
        )
    found = _channel_of(conn, cid)
    try:
        api.bot(
            "DELETE",
            f"/channels/{cid}",
            reason=f"Hubra: gelöscht von {who or 'einem Admin'}",
        )
    except DiscordError as exc:
        if exc.status != 404:
            botlog.log(conn, "channels", False, f"#{found.get('name')} löschen: {exc}")
            raise _error(exc, "Kanal nicht gelöscht") from None
    config[MADE_KEY] = [c for c in config.get(MADE_KEY) or [] if str(c) != cid]
    config["group_channels"] = [
        g for g in config.get("group_channels") or [] if str(g.get("channel_id")) != cid
    ]
    conn.config = config
    botlog.log(conn, "channels", True, f"#{found.get('name')} gelöscht ({who})")


# ── Messages the bot keeps up to date ────────────────────────────────────────


def messages(conn) -> list[dict]:
    """The posted event and poll messages (live.py), newest first."""
    from hubra.core.models.event import ClubEvent
    from hubra.core.models.poll import Poll

    from .live import posted

    known = posted(conn)
    event_ids = [int(k[2:]) for k in known if k.startswith("e:") and k[2:].isdigit()]
    poll_ids = [int(k[2:]) for k in known if k.startswith("p:") and k[2:].isdigit()]
    events = (
        {
            e.id: e
            for e in db.session.query(ClubEvent).filter(ClubEvent.id.in_(event_ids))
        }
        if event_ids
        else {}
    )
    polls = (
        {p.id: p for p in db.session.query(Poll).filter(Poll.id.in_(poll_ids))}
        if poll_ids
        else {}
    )
    guild = conn.external_id
    out = []
    for key in reversed(list(known)):
        kind, _, raw = key.partition(":")
        item = events.get(int(raw)) if kind == "e" else polls.get(int(raw))
        if kind == "e":
            title = item.title if item else "(gelöschter Anlass)"
            when = item.date.isoformat() if item else None
        else:
            title = item.question if item else "(gelöschte Umfrage)"
            when = item.closes_at.isoformat() if item and item.closes_at else None
        out.append(
            {
                "key": key,
                "kind": "event" if kind == "e" else "poll",
                "title": title,
                "when": when,
                "exists": item is not None,
                "messages": [
                    {
                        "channelId": ch,
                        "url": f"https://discord.com/channels/{guild}/{ch}/{msg}",
                    }
                    for ch, msg in known[key]
                ],
            }
        )
    return out


def refresh(conn, key: str) -> int:
    """Bring one entry's messages up to date now."""
    from .live import finish_poll, forget, posted, refresh_event

    if key not in posted(conn):
        raise ServerError("Diese Nachricht hält der Bot nicht mehr aktuell.")
    kind, _, raw = key.partition(":")
    if not raw.isdigit():
        raise ServerError("Unbekannter Eintrag.")
    if kind == "e":
        return refresh_event(conn, int(raw))
    done = finish_poll(conn, int(raw))
    if not done and key in posted(conn):
        # Still open: nothing to change yet.
        return 0
    forget(conn, key)
    return done


def stop(conn, key: str) -> None:
    """Stop keeping an entry up to date (the messages stay on Discord)."""
    from .live import forget

    forget(conn, key)
    botlog.log(conn, "updated", True, f"{key}: nicht mehr aktualisiert")
