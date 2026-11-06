"""Reminding those who still have to vote, on Discord.

With ``nudge_voters`` on, the "Schliesst bald" post of a required poll
mentions the linked members who haven't voted yet, so Discord notifies just
them.  Only who still has to vote is shown, never what anyone voted; people
who didn't link Discord are counted, not named.  The app's own reminder
(bell, mail, direct message by choice) goes out as before.
"""

from __future__ import annotations

from sqlalchemy import select

from hubra.core.extensions import db

MAX_MENTIONS = 50


def missing_voters(poll) -> list[int]:
    """Who the poll is for and hasn't voted (a proxy's vote counts)."""
    from hubra.core.contracts import SYSTEM, query

    info = query("polls.poll.get", poll_id=poll.id, actor=SYSTEM)
    return info["missing_member_ids"]


def discord_ids(member_ids: list[int]) -> list[str]:
    """Linked Discord accounts of these members (in the current club)."""
    from hubra.core.models.connected_app import ExternalAccount
    from hubra.core.models.member import Member

    if not member_ids:
        return []
    return [
        str(x)
        for x in db.session.scalars(
            select(ExternalAccount.external_id)
            .join(Member, Member.user_id == ExternalAccount.user_id)
            .where(
                ExternalAccount.provider == "discord",
                Member.id.in_(member_ids),
                Member.archived_at.is_(None),
            )
        )
    ]


def add_mentions(conn, poll, body: dict) -> dict:
    if not (conn.config or {}).get("nudge_voters"):
        return body
    missing = missing_voters(poll)
    if not missing:
        return body
    ids = discord_ids(missing)[:MAX_MENTIONS]
    others = len(missing) - len(ids)
    parts = [f"<@{i}>" for i in ids]
    text = "🗳️ Noch nicht abgestimmt: " + " ".join(parts) if parts else ""
    if others > 0:
        text += f"{' und ' if parts else '🗳️ Noch nicht abgestimmt: '}{others} ohne verknüpftes Discord"
    body = dict(body)
    body["content"] = (f"{body['content']} " if body.get("content") else "") + text
    allowed = dict(body.get("allowed_mentions") or {"parse": []})
    allowed["users"] = ids
    allowed["parse"] = [p for p in allowed.get("parse", []) if p != "users"]
    body["allowed_mentions"] = allowed
    return body
