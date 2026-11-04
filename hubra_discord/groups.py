"""Private channels for groups and functions.

The club picks groups and functions (Vorstand, Kassier, ...). For each, the
bot makes a role named after it (or takes the role the club already maps to
it) and one or more text channels that only that role and the bot can see,
optionally inside a category. Members who linked Discord get the role while
they are in the group or hold the function (role sync, roles.py), so the
channels follow the club. New events of a group, polls for a group or a
function and their reminders are posted in its first channel.

When the club takes a channel off the list, the channel and the role stay
on Discord with their history; the bot just stops managing them.

Stored as ``group_channels``: [{source, group_id | function_id, channel_id,
role_id, name, channel_name, category_id}]. Entries from before 2.6 have
only ``group_id``; :func:`source_of` reads both.
"""

from __future__ import annotations

import re

from sqlalchemy import select

from hubra.core import apps
from hubra.core.extensions import db

from . import api

MAX_GROUP_CHANNELS = 40
KINDS = ("group", "function")


def source_of(entry: dict) -> str:
    if entry.get("source"):
        return str(entry["source"])
    return f"group:{entry.get('group_id')}"


def entries(conn) -> list[dict]:
    """The group and function channels in use (needs the ``channels``
    scope)."""
    if not apps.has_scope(conn, "channels"):
        return []
    return [
        e
        for e in (conn.config or {}).get("group_channels") or []
        if e.get("channel_id") and e.get("role_id")
    ]


def channel_for_source(conn, source: str | None) -> str | None:
    """The first channel of a group or function ("group:3", "function:2")."""
    if not source:
        return None
    for e in entries(conn):
        if source_of(e) == source:
            return e["channel_id"]
    return None


def channel_for_group(conn, group_id: int | None) -> str | None:
    if group_id is None:
        return None
    return channel_for_source(conn, f"group:{int(group_id)}")


def channel_name(name: str) -> str:
    slug = re.sub(r"[^\w-]+", "-", name.lower(), flags=re.UNICODE).strip("-")
    return (slug or "gruppe")[:100]


def _parse(source: str) -> tuple[str, int]:
    kind, _, raw = str(source or "").partition(":")
    if kind not in KINDS or not raw.isdigit():
        raise apps.AppError(f"Unknown group or function: {source}")
    return kind, int(raw)


def _names(sources: set[str]) -> dict[str, str]:
    """source → the group's or function's name, for those that exist."""
    from hubra.core.models.function import Function
    from hubra.core.models.group import Group

    out: dict[str, str] = {}
    by_kind: dict[str, list[int]] = {"group": [], "function": []}
    for s in sources:
        kind, ident = _parse(s)
        by_kind[kind].append(ident)
    for model, kind in ((Group, "group"), (Function, "function")):
        if by_kind[kind]:
            for ident, name in db.session.execute(
                select(model.id, model.name).where(model.id.in_(by_kind[kind]))
            ).all():
                out[f"{kind}:{ident}"] = name
    return out


def _role_for(conn, source: str, name: str, role_map: list[dict], kept: list[dict]):
    """The role a source's channel is for: one already used by another of
    its channels, one the club maps to it, its automatic role, or a new one."""
    from .group_roles import role_sources as auto_roles

    for e in kept:
        if source_of(e) == source:
            return e["role_id"]
    for m in list(role_map) + auto_roles(conn):
        if m.get("source") == source:
            return m["role_id"]
    reason = f"Hubra: role for {name}"
    return str(api.create_role(conn.external_id, name, reason)["id"])


def create(
    conn,
    source: str,
    name: str,
    role_map: list[dict],
    *,
    channel: str = "",
    category_id: str | None = None,
    kept: list[dict] | None = None,
) -> dict:
    """Make the role (unless there is one) and the private channel. Returns
    the entry to store."""
    kind, ident = _parse(source)
    guild = conn.external_id
    role_id = _role_for(conn, source, name, role_map, kept or [])
    allow_members = api.VIEW_CHANNEL | api.SEND_MESSAGES | api.READ_MESSAGE_HISTORY
    allow_bot = allow_members | api.EMBED_LINKS
    body = {
        "name": channel_name(channel or name),
        "type": 0,
        "topic": f"{name} (Hubra)"[:1024],
        "permission_overwrites": [
            {"id": str(guild), "type": 0, "deny": str(api.VIEW_CHANNEL)},
            {"id": role_id, "type": 0, "allow": str(allow_members)},
            {"id": api.config("client_id"), "type": 1, "allow": str(allow_bot)},
        ],
    }
    if category_id:
        body["parent_id"] = str(category_id)
    made = api.create_channel(guild, body, f"Hubra: channel for {name}")
    entry = {
        "source": source,
        f"{kind}_id": ident,
        "channel_id": str(made["id"]),
        "role_id": role_id,
        "name": name,
        "channel_name": made.get("name", body["name"]),
        "category_id": str(category_id) if category_id else None,
    }
    return entry


def _wanted(picked) -> list[dict]:
    """The club's list, as dicts. Plain ids are groups (the form before 2.6)."""
    if not isinstance(picked, list) or len(picked) > MAX_GROUP_CHANNELS:
        raise apps.AppError(f"Up to {MAX_GROUP_CHANNELS} group channels")
    out = []
    for item in picked:
        if isinstance(item, dict):
            source = str(item.get("source") or "")
            if not source and item.get("group_id") is not None:
                source = f"group:{item['group_id']}"
            _parse(source)
            category = item.get("categoryId", item.get("category_id"))
            out.append(
                {
                    "source": source,
                    "channel_id": str(
                        item.get("channelId") or item.get("channel_id") or ""
                    )
                    or None,
                    "channel": str(item.get("name") or item.get("channel_name") or "")[
                        :100
                    ],
                    "category_id": str(category) if category else None,
                }
            )
            continue
        try:
            out.append(
                {
                    "source": f"group:{int(item)}",
                    "channel_id": None,
                    "channel": "",
                    "category_id": None,
                    "legacy": True,
                }
            )
        except (TypeError, ValueError) as exc:
            raise apps.AppError("Group channels are a list of groups") from exc
    return out


def update(conn, picked, role_map: list[dict]) -> list[dict]:
    """Validate the club's list; create what is new, move what changed
    category. Returns the entries to store. Raises ``apps.AppError``."""
    wanted = _wanted(picked)
    names = _names({w["source"] for w in wanted})
    unknown = {w["source"] for w in wanted} - set(names)
    if unknown:
        raise apps.AppError("Unknown group or function")
    categories = {w["category_id"] for w in wanted if w["category_id"]}
    if categories:
        try:
            on_server = {
                str(c["id"])
                for c in api.channels(conn.external_id)
                if c.get("type") == 4
            }
        except api.DiscordError as exc:
            from .app import _explain

            raise apps.AppError(_explain(exc)) from exc
        if categories - on_server:
            raise apps.AppError("That category isn't on the server")
    current = list((conn.config or {}).get("group_channels") or [])
    by_channel = {str(e.get("channel_id")): e for e in current if e.get("channel_id")}
    out: list[dict] = []
    try:
        for w in wanted:
            source, name = w["source"], names[w["source"]]
            entry = by_channel.get(str(w["channel_id"])) if w["channel_id"] else None
            if entry is None and w.get("legacy"):
                # A group id only: its first channel, if it has one.
                entry = next(
                    (e for e in current if source_of(e) == source and e not in out),
                    None,
                )
            if entry is not None and source_of(entry) == source:
                still = api.channel(entry["channel_id"])
                if still is not None:
                    entry = {**entry, "source": source, "name": name}
                    if not w.get("legacy") and (w["category_id"] or None) != (
                        str(still.get("parent_id")) if still.get("parent_id") else None
                    ):
                        api.edit_channel(
                            entry["channel_id"],
                            {"parent_id": w["category_id"]},
                            f"Hubra: channel for {name} moved",
                        )
                    if not w.get("legacy"):
                        entry["category_id"] = w["category_id"]
                    entry["channel_name"] = still.get("name", entry.get("channel_name"))
                    out.append(entry)
                    continue
            out.append(
                create(
                    conn,
                    source,
                    name,
                    role_map,
                    channel=w["channel"],
                    category_id=w["category_id"],
                    kept=out,
                )
            )
    except api.DiscordError as exc:
        from .app import _explain

        raise apps.AppError(_explain(exc)) from exc
    return out


def role_sources(conn) -> list[dict]:
    """Channel roles as role-map entries, for role sync (one per role)."""
    seen, out = set(), []
    for e in entries(conn):
        key = (source_of(e), e["role_id"])
        if key not in seen:
            seen.add(key)
            out.append({"source": key[0], "role_id": key[1]})
    return out
