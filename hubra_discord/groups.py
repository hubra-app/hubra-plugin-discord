"""Private group channels: a channel per group, only for that group.

The club picks groups; for each, the bot makes a role named after the group
(or takes the role the club already maps to it) and a text channel that only
that role and the bot can see. Members who linked Discord get the role while
they are in the group (role sync, roles.py), so the channel follows the
group. New events of the group, its polls and their reminders are posted
there.

When the club takes a group off the list, the channel and the role stay on
Discord with their history; the bot just stops managing them.
"""

from __future__ import annotations

import re

from sqlalchemy import select

from hubra.core import apps
from hubra.core.extensions import db

from . import api

MAX_GROUP_CHANNELS = 25


def entries(conn) -> list[dict]:
    """The group channels in use (needs the ``channels`` scope)."""
    if not apps.has_scope(conn, "channels"):
        return []
    return [
        e
        for e in (conn.config or {}).get("group_channels") or []
        if e.get("channel_id") and e.get("role_id")
    ]


def channel_for_group(conn, group_id: int | None) -> str | None:
    if group_id is None:
        return None
    for e in entries(conn):
        if int(e["group_id"]) == int(group_id):
            return e["channel_id"]
    return None


def channel_name(name: str) -> str:
    slug = re.sub(r"[^\w-]+", "-", name.lower(), flags=re.UNICODE).strip("-")
    return (slug or "gruppe")[:100]


def _group(group_id: int):
    from hubra.core.models.group import Group

    return db.session.get(Group, group_id)


def create(conn, group_id: int, role_map: list[dict]) -> dict:
    """Make the role (unless one is mapped to the group already) and the
    private channel. Returns the entry to store."""
    group = _group(group_id)
    if group is None:
        raise apps.AppError(f"There is no group {group_id}")
    guild = conn.external_id
    reason = f"Hubra: channel for the group {group.name}"
    # A role the club maps to the group, or the group's automatic role.
    from .group_roles import role_sources as auto_roles

    role_id = next(
        (
            m["role_id"]
            for m in list(role_map) + auto_roles(conn)
            if m.get("source") == f"group:{group_id}"
        ),
        None,
    )
    if role_id is None:
        role_id = str(api.create_role(guild, group.name, reason)["id"])
    allow_members = api.VIEW_CHANNEL | api.SEND_MESSAGES | api.READ_MESSAGE_HISTORY
    allow_bot = allow_members | api.EMBED_LINKS
    channel = api.create_channel(
        guild,
        {
            "name": channel_name(group.name),
            "type": 0,
            "topic": f"{group.name} (Hubra)"[:1024],
            "permission_overwrites": [
                {"id": str(guild), "type": 0, "deny": str(api.VIEW_CHANNEL)},
                {"id": role_id, "type": 0, "allow": str(allow_members)},
                {"id": api.config("client_id"), "type": 1, "allow": str(allow_bot)},
            ],
        },
        reason,
    )
    return {
        "group_id": group_id,
        "channel_id": str(channel["id"]),
        "role_id": role_id,
        "name": group.name,
    }


def update(conn, picked, role_map: list[dict]) -> list[dict]:
    """Validate the club's list of group ids; create what is new. Returns
    the entries to store. Raises ``apps.AppError``."""
    from hubra.core.models.group import Group

    if not isinstance(picked, list) or len(picked) > MAX_GROUP_CHANNELS:
        raise apps.AppError(f"Up to {MAX_GROUP_CHANNELS} group channels")
    try:
        ids = list(dict.fromkeys(int(g) for g in picked))
    except (TypeError, ValueError) as exc:
        raise apps.AppError("Group channels are a list of group ids") from exc
    known = set(db.session.scalars(select(Group.id).where(Group.id.in_(ids))).all())
    if set(ids) - known:
        raise apps.AppError("Unknown group")
    current = {
        int(e["group_id"]): e for e in (conn.config or {}).get("group_channels") or []
    }
    out = []
    try:
        for gid in ids:
            entry = current.get(gid)
            if entry and entry.get("channel_id"):
                still = api.channel(entry["channel_id"])
                if still is not None:
                    out.append(entry)
                    continue
            out.append(create(conn, gid, role_map))
    except api.DiscordError as exc:
        from .app import _explain

        raise apps.AppError(_explain(exc)) from exc
    return out


def role_sources(conn) -> list[dict]:
    """Group channel roles as role-map entries, for role sync."""
    return [
        {"source": f"group:{e['group_id']}", "role_id": e["role_id"]}
        for e in entries(conn)
    ]
