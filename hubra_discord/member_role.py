"""The member role: one Discord role for everyone who is a member now.

Club servers are mostly for members only. With "Mitglied-Rolle" on
(``member_role``, needs the ``roles`` or ``channels`` scope) the bot makes a
role (default "Mitglied") and every linked, active member gets it; it goes
when they leave, are archived or blocked (role sync, roles.py). Channels
that are for members only let this role see them (layout.py, audit.py).

Kept as ``auto_member_role``: {role_id, name}. Turning the switch off
deletes the role, unless the club uses it elsewhere (role map, a channel's
permissions it set itself).
"""

from __future__ import annotations

from hubra.core import apps

from . import api

DEFAULT_NAME = "Mitglied"
REASON = "Hubra: role for the club's members"


def on(conn) -> bool:
    return bool((conn.config or {}).get("member_role")) and (
        apps.has_scope(conn, "roles") or apps.has_scope(conn, "channels")
    )


def role_id(conn) -> str | None:
    if not on(conn):
        return None
    return ((conn.config or {}).get("auto_member_role") or {}).get("role_id")


def role_sources(conn) -> list[dict]:
    rid = role_id(conn)
    return [{"source": "members", "role_id": rid}] if rid else []


def missing(conn) -> bool:
    stored = (conn.config or {}).get("auto_member_role") or {}
    return on(conn) != bool(stored.get("role_id"))


def _store(conn, value: dict | None) -> None:
    config = dict(conn.config or {})
    config["auto_member_role"] = value or {}
    conn.config = config


def ensure(conn) -> list[str]:
    """Make, rename or delete the member role. Raises ``api.DiscordError``."""
    config = conn.config or {}
    stored = dict(config.get("auto_member_role") or {})
    name = (str(config.get("member_role_name") or "").strip() or DEFAULT_NAME)[:100]
    guild = conn.external_id
    if not on(conn):
        if not stored.get("role_id"):
            return []
        used = {str(m.get("role_id")) for m in config.get("role_map") or []}
        if stored["role_id"] not in used:
            api.delete_role(guild, stored["role_id"], "Hubra: member role off")
        _store(conn, None)
        return [f"−@{stored.get('name') or name}"]
    server = {r["id"]: r for r in api.roles(guild)}
    if stored.get("role_id") in server:
        if server[stored["role_id"]].get("name") != name:
            api.edit_role(guild, stored["role_id"], {"name": name}, REASON)
            _store(conn, {"role_id": stored["role_id"], "name": name})
            return [f"renamed @{stored.get('name')} → @{name}"]
        return []
    # Take a role with that name the club made already, else make one.
    same = next(
        (
            r
            for r in server.values()
            if r.get("name") == name and not r.get("managed") and r["id"] != guild
        ),
        None,
    )
    role = same or api.create_role(guild, name, REASON)
    _store(conn, {"role_id": str(role["id"]), "name": name})
    return [f"{'=' if same else '+'}@{name}"]


def ensure_now(conn) -> str:
    """For layout.py and the audit's fix: the role id, made if needed."""
    if not (conn.config or {}).get("member_role"):
        config = dict(conn.config or {})
        config["member_role"] = True
        conn.config = config
    ensure(conn)
    rid = role_id(conn)
    if not rid:
        raise api.DiscordError("The member role could not be made")
    return rid
