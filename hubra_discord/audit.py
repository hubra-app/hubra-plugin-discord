"""Permission audit of the club's Discord server.

Club servers are for members. The audit goes through every channel and
says who can see it:

- ``public``: anyone on the server (also people who are not members). Fine
  only when a club admin approved it in Hubra (``public_ok``); otherwise it
  is a finding, with "Nur für Mitglieder" as the fix.
- ``members``: the member role (member_role.py) can see it, @everyone not.
- ``private``: only some roles (a group, the board, ...).

It also flags what is risky on the whole server: @everyone with
Administrator, Manage Server/Roles/Channels or @everyone mentions, a group
or function channel anyone can see, and members-only events going to a
public channel.

The fix sets two permission overwrites on the channel: @everyone may not
view it, the member role may. Nothing else on the channel changes.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import api, botlog
from .api import DiscordError

MANAGE_GUILD = 1 << 5
MENTION_EVERYONE = 1 << 17
CONNECT = 1 << 20
RISKY_EVERYONE = {
    api.ADMINISTRATOR: "Administrator",
    MANAGE_GUILD: "Server verwalten",
    api.MANAGE_ROLES: "Rollen verwalten",
    api.MANAGE_CHANNELS: "Kanäle verwalten",
    MENTION_EVERYONE: "@everyone erwähnen",
}
MAX_APPROVED = 100


class AuditError(Exception):
    """Something a club admin can fix, in their words."""


def _approved(config: dict) -> dict[str, dict]:
    return {str(a.get("id")): a for a in config.get("public_ok") or [] if a.get("id")}


def _view_for(channel: dict, role_id: str, base: int) -> bool:
    """Whether a role (with server-wide bits *base*) sees the channel, with
    @everyone's overwrite and the role's own applied."""
    if base & api.ADMINISTRATOR:
        return True
    bits = base
    overwrites = {str(o["id"]): o for o in channel.get("permission_overwrites") or []}
    guild = str(channel.get("guild_id") or "")
    for oid in (guild, role_id) if role_id != guild else (guild,):
        o = overwrites.get(oid)
        if o:
            bits = (bits & ~int(o.get("deny") or 0)) | int(o.get("allow") or 0)
    return bool(bits & api.VIEW_CHANNEL)


def run(conn) -> dict:
    """The audit, read live from Discord. Raises ``AuditError``."""
    from .member_role import role_id as member_role_id
    from .server import TYPES, uses

    guild = str(conn.external_id)
    config = conn.config or {}
    try:
        channels = api.channels(guild)
        roles = {r["id"]: r for r in api.roles(guild)}
    except DiscordError as exc:
        raise AuditError(f"Discord hat die Kanäle nicht geschickt: {exc}") from None
    everyone = int((roles.get(guild) or {}).get("permissions") or 0)
    member_role = member_role_id(conn)
    member_bits = everyone | int(
        (roles.get(member_role or "") or {}).get("permissions") or 0
    )
    approved = _approved(config)
    used = uses(config)
    group_channels = {
        str(g.get("channel_id")) for g in config.get("group_channels") or []
    }
    hubra_posts = {
        str(c)
        for c in [config.get("channel_id"), *(config.get("channels") or {}).values()]
        if c
    }
    public_events = str((config.get("channels") or {}).get("public_events") or "")
    rows, findings = [], []
    for c in channels:
        c = {**c, "guild_id": guild}
        kind = TYPES.get(c.get("type"), "other")
        cid = str(c["id"])
        public = _view_for(c, guild, everyone)
        members = member_role is not None and _view_for(c, member_role, member_bits)
        access = "public" if public else "members" if members else "private"
        row = {
            "id": cid,
            "name": c.get("name", ""),
            "kind": kind,
            "parentId": str(c["parent_id"]) if c.get("parent_id") else None,
            "access": access,
            "uses": used.get(cid, []),
            "approved": None,
            "problem": None,
            "url": f"https://discord.com/channels/{guild}/{cid}",
        }
        if public and cid in approved:
            a = approved[cid]
            row["approved"] = {"by": a.get("by"), "at": a.get("at")}
        if kind != "category":
            if public and cid in group_channels:
                row["problem"] = (
                    "Gruppen- oder Funktionskanal, den alle auf dem Server sehen"
                )
            elif (
                public
                and cid in hubra_posts
                and cid != public_events
                and config.get("members_only_events")
            ):
                row["problem"] = (
                    "Anlässe nur für Mitglieder gehen in diesen Kanal, den alle sehen"
                )
            elif public and cid not in approved:
                row["problem"] = "Öffentlich, aber nicht freigegeben"
        if row["problem"]:
            findings.append({"channelId": cid, "text": row["problem"]})
        rows.append(row)
    risky = [name for bit, name in RISKY_EVERYONE.items() if everyone & bit]
    server = []
    if risky:
        server.append(
            "@everyone darf auf dem ganzen Server: "
            + ", ".join(risky)
            + ". Das sollten nur Admin-Rollen dürfen."
        )
    if member_role is None:
        server.append(
            "Es gibt keine Mitglied-Rolle. Schalte sie unter Rollen ein, dann "
            "sehen Mitglieder die Kanäle «nur für Mitglieder»."
        )
    return {
        "channels": rows,
        "findings": findings,
        "server": server,
        "memberRole": member_role,
        "checkedAt": datetime.now(timezone.utc).isoformat(),
    }


def _channel(conn, channel_id: str) -> dict:
    from .server import ServerError, _channel_of

    try:
        return _channel_of(conn, channel_id)
    except ServerError as exc:
        raise AuditError(str(exc)) from None


def approve(conn, channel_id: str, who: str) -> None:
    """Keep this channel public: the audit stops flagging it."""
    found = _channel(conn, channel_id)
    config = dict(conn.config or {})
    kept = [a for a in config.get("public_ok") or [] if str(a.get("id")) != channel_id]
    kept.append(
        {
            "id": str(channel_id),
            "name": found.get("name", ""),
            "by": who,
            "at": datetime.now(timezone.utc).isoformat(),
        }
    )
    config["public_ok"] = kept[-MAX_APPROVED:]
    conn.config = config
    botlog.log(
        conn, "audit", True, f"#{found.get('name')} öffentlich freigegeben ({who})"
    )


def revoke(conn, channel_id: str, who: str) -> None:
    config = dict(conn.config or {})
    before = config.get("public_ok") or []
    config["public_ok"] = [a for a in before if str(a.get("id")) != str(channel_id)]
    if len(config["public_ok"]) == len(before):
        raise AuditError("Dieser Kanal war nicht freigegeben.")
    conn.config = config
    botlog.log(
        conn, "audit", True, f"Freigabe für #{channel_id} zurückgenommen ({who})"
    )


def members_only(conn, channel_id: str, who: str) -> None:
    """@everyone may not view the channel, the member role may."""
    from . import member_role

    found = _channel(conn, channel_id)
    try:
        role = member_role.ensure_now(conn)
        overwrites = {str(o["id"]): o for o in found.get("permission_overwrites") or []}
        mine = overwrites.get(str(conn.external_id)) or {}
        reason = f"Hubra: nur für Mitglieder ({who})"
        api.bot(
            "PUT",
            f"/channels/{channel_id}/permissions/{conn.external_id}",
            {
                "type": 0,
                "allow": str(int(mine.get("allow") or 0) & ~api.VIEW_CHANNEL),
                "deny": str(int(mine.get("deny") or 0) | api.VIEW_CHANNEL),
            },
            reason=reason,
        )
        theirs = overwrites.get(role) or {}
        see = api.VIEW_CHANNEL | api.READ_MESSAGE_HISTORY
        if found.get("type") in (2, 13):
            see |= CONNECT
        api.bot(
            "PUT",
            f"/channels/{channel_id}/permissions/{role}",
            {
                "type": 0,
                "allow": str(int(theirs.get("allow") or 0) | see),
                "deny": str(int(theirs.get("deny") or 0) & ~see),
            },
            reason=reason,
        )
        # The bot keeps seeing it.
        bot_id = str(api.config("client_id"))
        if bot_id not in overwrites:
            api.bot(
                "PUT",
                f"/channels/{channel_id}/permissions/{bot_id}",
                {
                    "type": 1,
                    "allow": str(
                        api.VIEW_CHANNEL
                        | api.SEND_MESSAGES
                        | api.EMBED_LINKS
                        | api.READ_MESSAGE_HISTORY
                    ),
                    "deny": "0",
                },
                reason=reason,
            )
    except DiscordError as exc:
        botlog.log(
            conn, "audit", False, f"#{found.get('name')} nur für Mitglieder: {exc}"
        )
        if exc.status == 403:
            raise AuditError(
                "Der Hubra-Rolle fehlt «Rollen verwalten» in diesem Kanal. Gib "
                "sie ihr oder füge den Bot erneut hinzu."
            ) from None
        raise AuditError(str(exc)) from None
    revoke_quietly(conn, channel_id)
    botlog.log(conn, "audit", True, f"#{found.get('name')} nur für Mitglieder ({who})")


def revoke_quietly(conn, channel_id: str) -> None:
    config = dict(conn.config or {})
    config["public_ok"] = [
        a for a in config.get("public_ok") or [] if str(a.get("id")) != str(channel_id)
    ]
    conn.config = config


def nightly(conn) -> int:
    """For the nightly run: new findings go to the bot log (and so to the
    club's log channel); ones already reported are not repeated. Returns
    how many are new."""
    try:
        result = run(conn)
    except AuditError as exc:
        botlog.log(conn, "audit", False, str(exc))
        return 0
    names = {c["id"]: c["name"] for c in result["channels"]}
    now = {f"{f['channelId']}:{f['text']}": f for f in result["findings"]}
    now.update({f"server:{t}": None for t in result["server"][:1] if "@everyone" in t})
    seen = set((conn.config or {}).get("audit_seen") or [])
    new = [k for k in now if k not in seen]
    for key in new:
        f = now[key]
        if f is None:
            botlog.log(conn, "audit", False, key.partition(":")[2])
        else:
            botlog.log(
                conn, "audit", False, f"#{names.get(f['channelId'], '')}: {f['text']}"
            )
    if set(now) != seen:
        config = dict(conn.config or {})
        config["audit_seen"] = sorted(now)[:200]
        conn.config = config
    return len(new)
