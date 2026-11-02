"""Automatic group roles: one Discord role per group, kept in sync.

With "Rolle für jede Gruppe" on (``group_roles``, needs the ``roles``
scope), the bot makes a role for every open or visible group of the club,
named after the group, and members who linked Discord get it while they
are in the group (role sync, roles.py). Private groups get no automatic
role: everyone on the server sees who holds a role, and a private group's
members are not public in the club either. A club can still map a private
group to a role by hand.

The bot renames a role when its group is renamed, and deletes the role when
the group is deleted or archived, turns private, or when the club turns the
switch off. Groups the club already maps to a role (role map) or that have
a group channel keep that role instead; when a group gets a channel, the
channel takes over the automatic role.

Roles are made without permissions and are not mentionable. Kept in the
connection's config as ``auto_group_roles``: [{group_id, role_id, name}].
"""

from __future__ import annotations

from sqlalchemy import select

from hubra.core import apps
from hubra.core.extensions import db

from . import api

MAX_GROUP_ROLES = 50
REASON = "Hubra: role for the group {}"


def on(conn) -> bool:
    return bool((conn.config or {}).get("group_roles")) and apps.has_scope(
        conn, "roles"
    )


def entries(conn) -> list[dict]:
    """The automatic roles in use."""
    if not on(conn):
        return []
    return [
        e
        for e in (conn.config or {}).get("auto_group_roles") or []
        if e.get("role_id") and e.get("group_id") is not None
    ]


def role_sources(conn) -> list[dict]:
    """Automatic roles as role-map entries, for role sync."""
    return [
        {"source": f"group:{e['group_id']}", "role_id": e["role_id"]}
        for e in entries(conn)
    ]


def _taken(conn) -> tuple[set[int], set[str]]:
    """Groups that have a role some other way, and the roles in such use."""
    from .groups import entries as channel_entries

    config = conn.config or {}
    groups, roles = set(), set()
    for m in config.get("role_map") or []:
        kind, _, raw = str(m.get("source", "")).partition(":")
        if kind == "group" and raw.isdigit():
            groups.add(int(raw))
        roles.add(str(m.get("role_id")))
    for e in channel_entries(conn):
        groups.add(int(e["group_id"]))
        roles.add(str(e["role_id"]))
    return groups, roles


def eligible(conn) -> dict[int, str]:
    """{group id: name} of the groups that should have an automatic role."""
    from hubra.core.models.group import Group

    query = select(Group.id, Group.name).where(Group.visibility != "private")
    for column in ("deleted_at", "archived_at"):
        if hasattr(Group, column):
            query = query.where(getattr(Group, column).is_(None))
    taken, _ = _taken(conn)
    rows = db.session.execute(query.order_by(Group.name)).all()
    out = {gid: name for gid, name in rows if gid not in taken}
    return dict(list(out.items())[:MAX_GROUP_ROLES])


def missing(conn) -> bool:
    """A group without its role yet (no Discord call)."""
    if not on(conn):
        return bool((conn.config or {}).get("auto_group_roles"))
    have = {int(e["group_id"]) for e in entries(conn)}
    return bool(set(eligible(conn)) - have)


def _store(conn, kept: list[dict]) -> None:
    config = dict(conn.config or {})
    config["auto_group_roles"] = kept
    conn.config = config


def ensure(conn) -> list[str]:
    """Make, rename and delete the automatic roles. Returns what changed,
    as short texts for the connection's log. Raises ``api.DiscordError``."""
    guild = conn.external_id
    current = {
        int(e["group_id"]): e
        for e in (conn.config or {}).get("auto_group_roles") or []
        if e.get("role_id")
    }
    want = eligible(conn) if on(conn) else {}
    _, in_use = _taken(conn)
    server = {r["id"]: r for r in api.roles(guild)} if (want or current) else {}
    changes: list[str] = []
    kept: list[dict] = []
    for gid, name in want.items():
        entry = current.pop(gid, None)
        if entry and entry["role_id"] in server:
            if server[entry["role_id"]].get("name") != name[:100]:
                api.edit_role(
                    guild, entry["role_id"], {"name": name[:100]}, REASON.format(name)
                )
                changes.append(f"renamed @{entry.get('name')} → @{name}")
            kept.append({**entry, "name": name})
            continue
        role = api.create_role(guild, name, REASON.format(name))
        kept.append({"group_id": gid, "role_id": str(role["id"]), "name": name})
        changes.append(f"+@{name}")
    for entry in current.values():
        # Taken over by a group channel or the role map: keep the role.
        if entry["role_id"] not in in_use:
            api.delete_role(guild, entry["role_id"], "Hubra: group gone or private")
            changes.append(f"−@{entry.get('name') or entry['role_id']}")
    if kept != list((conn.config or {}).get("auto_group_roles") or []):
        _store(conn, kept)
    return changes
