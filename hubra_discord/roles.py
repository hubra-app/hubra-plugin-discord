"""Role sync: Discord roles follow the club's groups, functions and tiers.

The club maps sources to Discord roles (``role_map``). For a member who
linked their Discord account, the bot gives the mapped roles they should
have and takes away the mapped roles they shouldn't. Roles the club didn't
map are never touched. Runs inside the club (schema and ``g.tenant`` set).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select

from hubra.core import apps
from hubra.core.extensions import db

from . import api

MEMBERS = "members"


def sources() -> list[dict]:
    """What a Discord role can follow, for the settings form."""
    from hubra.core.models.function import Function
    from hubra.core.models.group import Group
    from hubra.core.models.member import MembershipTier

    out = [{"key": MEMBERS, "label": "Active members", "kind": "members"}]
    for g in db.session.scalars(select(Group).order_by(Group.name)).all():
        out.append({"key": f"group:{g.id}", "label": g.name, "kind": "group"})
    for f in db.session.scalars(select(Function).order_by(Function.name)).all():
        out.append({"key": f"function:{f.id}", "label": f.name, "kind": "function"})
    for t in db.session.scalars(
        select(MembershipTier).order_by(MembershipTier.name)
    ).all():
        out.append({"key": f"tier:{t.id}", "label": t.name, "kind": "tier"})
    return out


def _is_member_now(member) -> bool:
    return (
        member.archived_at is None
        and getattr(member, "deleted_at", None) is None
        and not member.is_blocked
        and member.status != "inactive"
    )


def matches(source: str, member) -> bool:
    from hubra.core.models.function import FunctionHolder
    from hubra.core.models.member import member_groups

    if not _is_member_now(member):
        return False
    if source == MEMBERS:
        return True
    kind, _, raw = source.partition(":")
    try:
        ident = int(raw)
    except ValueError:
        return False
    if kind == "group":
        return (
            db.session.scalar(
                select(member_groups.c.member_id).where(
                    member_groups.c.group_id == ident,
                    member_groups.c.member_id == member.id,
                )
            )
            is not None
        )
    if kind == "function":
        today = date.today()
        holders = db.session.scalars(
            select(FunctionHolder).where(
                FunctionHolder.function_id == ident,
                FunctionHolder.member_id == member.id,
            )
        ).all()
        return any(
            (h.term_start is None or h.term_start <= today)
            and (h.term_end is None or h.term_end >= today)
            for h in holders
        )
    if kind == "tier":
        return member.tier_id == ident
    return False


def discord_id(member) -> str | None:
    from hubra.core.models.connected_app import ExternalAccount

    if member.user_id is None:
        return None
    return db.session.scalar(
        select(ExternalAccount.external_id).where(
            ExternalAccount.user_id == member.user_id,
            ExternalAccount.provider == "discord",
        )
    )


def _names(guild_id: str) -> dict[str, str]:
    try:
        return {r["id"]: r["name"] for r in api.roles(guild_id)}
    except api.DiscordError:
        return {}


def apply(conn, user_id: str, want: set[str], label: str, names: dict | None = None):
    """Make the mapped roles of Discord user *user_id* equal *want*.

    Returns a short text of what changed, or None (no change, or the user
    isn't on the server)."""
    managed = {m["role_id"] for m in (conn.config or {}).get("role_map", [])}
    on_server = api.member(conn.external_id, user_id)
    if on_server is None:
        return None
    have = set(on_server.get("roles", [])) & managed
    want = want & managed
    names = names if names is not None else _names(conn.external_id)
    added, removed = sorted(want - have), sorted(have - want)
    for role in added:
        api.add_role(conn.external_id, user_id, role, f"Hubra: {label}")
    for role in removed:
        api.remove_role(conn.external_id, user_id, role, f"Hubra: {label}")
    if not added and not removed:
        return None
    parts = [f"+{names.get(r, r)}" for r in added] + [
        f"−{names.get(r, r)}" for r in removed
    ]
    return f"{label}: {', '.join(parts)}"


def wanted(conn, member) -> set[str]:
    return {
        m["role_id"]
        for m in (conn.config or {}).get("role_map", [])
        if matches(m["source"], member)
    }


def _name(member) -> str:
    return f"{member.first_name} {member.last_name}".strip() or f"#{member.id}"


def sync_member(conn, member, names: dict | None = None) -> str | None:
    if not apps.has_scope(conn, "roles"):
        return None
    user_id = discord_id(member)
    if not user_id:
        return None
    return apply(conn, user_id, wanted(conn, member), _name(member), names)


def sync_all(conn) -> int:
    """Every linked member of the club. Returns how many changed."""
    from hubra.core.models.connected_app import ExternalAccount
    from hubra.core.models.member import Member

    if not apps.has_scope(conn, "roles") or not (conn.config or {}).get("role_map"):
        return 0
    linked = set(
        db.session.scalars(
            select(ExternalAccount.user_id).where(ExternalAccount.provider == "discord")
        ).all()
    )
    names = _names(conn.external_id)
    changed = 0
    for member in db.session.scalars(
        select(Member).where(Member.user_id.in_(linked))
    ).all():
        detail = sync_member(conn, member, names)
        if detail:
            changed += 1
            apps.record(conn, "roles", True, detail)
    return changed
