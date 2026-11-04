"""People who left the club leave the server too (``kick_leavers``).

Needs the ``moderate`` scope (Kick Members). Every night, and when an admin
presses "Jetzt prüfen", the bot looks at the club's former members (archived
or deleted) who linked Discord, and removes those still on the server.
Guests who never were members are not touched, nor is anyone the bot
removed before (``kicked``), so someone who comes back as a guest stays.
The server's owner and people above the bot's role can't be removed;
Discord refuses and the bot log says so.
"""

from __future__ import annotations

from sqlalchemy import or_, select

from hubra.core import apps
from hubra.core.extensions import db

from . import api, botlog
from .api import DiscordError

MAX_PER_RUN = 25
MAX_KICKED = 500
REASON = "Hubra: no longer a member of the club"


class LeaversError(Exception):
    """Something a club admin can fix, in their words."""


def former(conn) -> list[tuple[object, str]]:
    """[(member, Discord user id)] of former members with a linked account,
    not removed before."""
    from hubra.core.models.connected_app import ExternalAccount
    from hubra.core.models.member import Member

    done = set((conn.config or {}).get("kicked") or [])
    rows = db.session.execute(
        select(Member, ExternalAccount.external_id)
        .join(ExternalAccount, ExternalAccount.user_id == Member.user_id)
        .where(
            ExternalAccount.provider == "discord",
            or_(Member.archived_at.is_not(None), Member.deleted_at.is_not(None)),
        )
        .order_by(Member.archived_at.desc())
    ).all()
    return [(m, str(uid)) for m, uid in rows if str(uid) not in done]


def require(conn) -> None:
    if not apps.has_scope(conn, "moderate"):
        raise LeaversError(
            "Moderation ist für diese Verbindung nicht freigegeben "
            "(Bereich «Moderation» unter Darf)."
        )


def check(conn) -> dict:
    """Who would go now: former members still on the server."""
    require(conn)
    out = []
    try:
        for member, uid in former(conn)[: MAX_PER_RUN * 2]:
            if api.member(conn.external_id, uid) is not None:
                out.append(
                    {"memberId": member.id, "name": member.name, "discordId": uid}
                )
    except DiscordError as exc:
        raise LeaversError(f"Discord hat nicht geantwortet: {exc}") from None
    return {
        "people": out[:MAX_PER_RUN],
        "on": bool((conn.config or {}).get("kick_leavers")),
    }


def run(conn, who: str = "") -> dict:
    """Remove former members still on the server. Returns the counts."""
    if not apps.has_scope(conn, "moderate"):
        return {"removed": 0, "failed": 0}
    removed, failed, names = 0, 0, []
    kicked = list((conn.config or {}).get("kicked") or [])
    reason = REASON + (f" ({who})" if who else "")
    for member, uid in former(conn)[: MAX_PER_RUN * 2]:
        if removed + failed >= MAX_PER_RUN:
            break
        try:
            if api.kick(conn.external_id, uid, reason):
                removed += 1
                names.append(member.name)
            kicked.append(uid)  # gone or not on it: done either way
        except DiscordError as exc:
            failed += 1
            botlog.log(conn, "leavers", False, f"{member.name}: {exc}")
    config = dict(conn.config or {})
    config["kicked"] = kicked[-MAX_KICKED:]
    conn.config = config
    if removed:
        botlog.log(conn, "leavers", True, "Vom Server entfernt: " + ", ".join(names))
    return {"removed": removed, "failed": failed}
