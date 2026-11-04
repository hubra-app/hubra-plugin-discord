"""Channel layout: the usual club channels in one go.

Settings › Verbundene Apps › Discord › Kanäle › "Kanäle einrichten" makes
the channels a club picks, in a new or an existing category, with the right
permissions, and wires them up in Hubra:

==============  ======================  ==========================  =====================
slot            name                    who sees it                 Hubra uses it for
==============  ======================  ==========================  =====================
rules           regeln                  everyone, read only         –
announcements   ankündigungen           members, read only          main channel
roles           rollen                  members, read only          "Gruppen wählen" button
events          anlässe                 members, read only          events
public_events   anlässe-öffentlich      everyone, read only         public events
bot             bot                     members, can write          commands
log             hubra-log               only the bot (and admins)   bot log
==============  ======================  ==========================  =====================

Public slots count as approved for the permission audit (the admin picked
them here). Members-only slots need the member role (member_role.py),
which is turned on and made if needed. A slot that already has its channel
is left alone. Kept as ``layout``: {slot: channel id}.
"""

from __future__ import annotations

from datetime import datetime, timezone

from hubra.core import apps

from . import api, botlog
from .api import DiscordError

READ = api.VIEW_CHANNEL | api.READ_MESSAGE_HISTORY
WRITE = READ | api.SEND_MESSAGES
BOT = WRITE | api.EMBED_LINKS
ADD_REACTIONS = 1 << 6
USE_COMMANDS = 1 << 31
SEND_IN_THREADS = 1 << 38

SLOTS = {
    "rules": {
        "name": "regeln",
        "topic": "Die Regeln unseres Vereins und dieses Servers",
        "access": "public_read",
    },
    "announcements": {
        "name": "ankündigungen",
        "topic": "Neuigkeiten aus dem Verein (Hubra)",
        "access": "members_read",
        "type": 5,
    },
    "roles": {
        "name": "rollen",
        "topic": "Gruppen wählen: Tippe auf den Knopf, dann wähle deine Gruppen",
        "access": "members_read",
    },
    "events": {
        "name": "anlässe",
        "topic": "Anlässe des Vereins: anmelden mit den Knöpfen",
        "access": "members_read",
    },
    "public_events": {
        "name": "anlässe-öffentlich",
        "topic": "Öffentliche Anlässe des Vereins",
        "access": "public_read",
    },
    "bot": {
        "name": "bot",
        "topic": "Befehle für den Hubra-Bot: /hilfe",
        "access": "members_write",
    },
    "log": {
        "name": "hubra-log",
        "topic": "Was der Hubra-Bot tut (nur für Admins)",
        "access": "private",
    },
}
ORDER = tuple(SLOTS)


class LayoutError(Exception):
    """Something a club admin can fix, in their words."""


def current(conn) -> dict:
    """Per slot: the channel it has, if that channel still exists."""
    config = conn.config or {}
    have = {str(k): str(v) for k, v in (config.get("layout") or {}).items() if v}
    try:
        on_server = {str(c["id"]): c for c in api.channels(conn.external_id)}
    except DiscordError as exc:
        raise LayoutError(f"Discord hat die Kanäle nicht geschickt: {exc}") from None
    categories = [
        {"id": str(c["id"]), "name": c.get("name", "")}
        for c in sorted(on_server.values(), key=lambda c: c.get("position", 0))
        if c.get("type") == 4
    ]
    slots = []
    for key in ORDER:
        cid = have.get(key)
        found = on_server.get(cid) if cid else None
        slots.append(
            {
                "key": key,
                "name": SLOTS[key]["name"],
                "access": SLOTS[key]["access"],
                "channelId": cid if found else None,
                "channelName": found.get("name") if found else None,
            }
        )
    return {
        "slots": slots,
        "categories": categories,
        "hasScope": apps.has_scope(conn, "channels"),
    }


def _overwrites(conn, access: str, member_role: str | None) -> list[dict]:
    guild = str(conn.external_id)
    bot_id = str(api.config("client_id"))
    out = [{"id": bot_id, "type": 1, "allow": str(BOT), "deny": "0"}]
    no_write = api.SEND_MESSAGES | SEND_IN_THREADS
    if access == "public_read":
        out.append({"id": guild, "type": 0, "allow": str(READ), "deny": str(no_write)})
        return out
    out.append({"id": guild, "type": 0, "allow": "0", "deny": str(api.VIEW_CHANNEL)})
    if access == "members_read" and member_role:
        out.append(
            {
                "id": member_role,
                "type": 0,
                "allow": str(READ | ADD_REACTIONS),
                "deny": str(no_write),
            }
        )
    elif access == "members_write" and member_role:
        out.append(
            {
                "id": member_role,
                "type": 0,
                "allow": str(WRITE | ADD_REACTIONS | USE_COMMANDS),
                "deny": "0",
            }
        )
    return out


def _roles_panel() -> dict:
    return {
        "embeds": [
            {
                "title": "Gruppen wählen",
                "description": (
                    "Tippe auf den Knopf: Du siehst die offenen Gruppen des "
                    "Vereins und trittst mit einem Tipp bei oder aus. Die "
                    "Discord-Rollen und Kanäle der Gruppe kommen von selbst.\n\n"
                    "Noch nicht verknüpft? Der Knopf zeigt dir, wie."
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
                        "style": 1,
                        "label": "Gruppen wählen",
                        "custom_id": "gpick",
                        "emoji": {"name": "🎭"},
                    }
                ],
            }
        ],
        "allowed_mentions": {"parse": []},
    }


def apply(
    conn,
    slots: list[str],
    *,
    category_id: str | None = None,
    category_name: str = "",
    who: str = "",
) -> dict:
    """Make the picked slots' channels. Returns what was made and skipped."""
    from . import member_role
    from .server import MADE_KEY, MAX_MADE

    if not apps.has_scope(conn, "channels"):
        raise LayoutError(
            "Kanäle verwalten ist für diese Verbindung nicht freigegeben "
            "(Bereich «Kanäle» unter Darf)."
        )
    picked = [s for s in ORDER if s in set(slots or [])]
    if not picked:
        raise LayoutError("Wähle mindestens einen Kanal.")
    state = current(conn)
    have = {s["key"]: s["channelId"] for s in state["slots"] if s["channelId"]}
    todo = [s for s in picked if s not in have]
    if not todo:
        return {"made": [], "kept": picked}
    guild = str(conn.external_id)
    reason = f"Hubra: Kanäle eingerichtet von {who or 'einem Admin'}"
    made: list[dict] = []
    try:
        needs_members = any(SLOTS[s]["access"].startswith("members") for s in todo)
        role = member_role.ensure_now(conn) if needs_members else None
        parent = None
        if category_id:
            if category_id not in {c["id"] for c in state["categories"]}:
                raise LayoutError("Diese Kategorie gibt es auf eurem Server nicht.")
            parent = str(category_id)
        elif category_name.strip():
            cat = api.create_channel(
                guild, {"name": category_name.strip()[:100], "type": 4}, reason
            )
            parent = str(cat["id"])
            made.append({"slot": "category", "id": parent, "name": cat.get("name")})
        for slot in todo:
            spec = SLOTS[slot]
            body = {
                "name": spec["name"],
                "type": spec.get("type", 0),
                "topic": spec["topic"],
                "permission_overwrites": _overwrites(conn, spec["access"], role),
            }
            if parent:
                body["parent_id"] = parent
            try:
                channel = api.create_channel(guild, body, reason)
            except DiscordError as exc:
                if body["type"] != 5 or exc.status != 400:
                    raise
                # Announcement channels need a Community server.
                body["type"] = 0
                channel = api.create_channel(guild, body, reason)
            made.append(
                {"slot": slot, "id": str(channel["id"]), "name": channel["name"]}
            )
    except DiscordError as exc:
        _store(conn, made, who)
        botlog.log(conn, "layout", False, f"Kanäle einrichten: {exc}")
        if exc.status == 403:
            raise LayoutError(
                "Der Hubra-Rolle fehlt «Kanäle verwalten» oder «Rollen "
                "verwalten». Füge den Bot erneut hinzu oder gib der Rolle die "
                "Rechte in den Servereinstellungen."
                + (
                    f" Angelegt: {', '.join('#' + m['name'] for m in made)}."
                    if made
                    else ""
                )
            ) from None
        raise LayoutError(str(exc)) from None
    _store(conn, made, who)
    config = dict(conn.config or {})
    config[MADE_KEY] = ([*(config.get(MADE_KEY) or []), *(m["id"] for m in made)])[
        -MAX_MADE:
    ]
    conn.config = config
    by_slot = {m["slot"]: m["id"] for m in made}
    if "roles" in by_slot and apps.has_scope(conn, "commands"):
        try:
            api.send(by_slot["roles"], _roles_panel())
        except DiscordError as exc:
            botlog.log(conn, "layout", False, f"#rollen: Knopf nicht gepostet: {exc}")
    botlog.log(
        conn,
        "layout",
        True,
        "Angelegt: " + ", ".join(f"#{m['name']}" for m in made) + f" ({who})",
    )
    return {"made": made, "kept": [s for s in picked if s in have]}


def _store(conn, made: list[dict], who: str) -> None:
    """Record the made channels and wire them up where nothing is set yet."""
    if not made:
        return
    config = dict(conn.config or {})
    layout = dict(config.get("layout") or {})
    channels = dict(config.get("channels") or {})
    approved = list(config.get("public_ok") or [])
    now = datetime.now(timezone.utc).isoformat()
    for m in made:
        slot, cid = m["slot"], m["id"]
        if slot == "category":
            continue
        layout[slot] = cid
        if SLOTS[slot]["access"] == "public_read":
            approved.append({"id": cid, "name": m["name"], "by": who, "at": now})
        if slot == "announcements" and not config.get("channel_id"):
            config["channel_id"] = cid
        if slot == "events":
            channels["events"] = cid
            # The channel is for members only: their events can go there.
            config["members_only_events"] = True
        if slot == "public_events":
            channels["public_events"] = cid
        if slot == "log" and not config.get("log_channel_id"):
            config["log_channel_id"] = cid
    config["layout"] = layout
    config["channels"] = channels
    config["public_ok"] = approved[-100:]
    conn.config = config
