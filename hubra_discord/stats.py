"""The club's numbers on Discord.

* **Stats channel** (``stats_channel``, ``channels`` scope): a voice channel
  at the top of the server whose name shows the member count, e.g.
  "👥 Mitglieder: 128". Everyone sees it, nobody can join it. The bot makes
  it when the switch goes on, renames it every night (Discord allows only a
  few renames per channel, so not more often) and deletes it when the switch
  goes off. Kept as ``stats_channel_id``.
* **Weekly numbers** (``weekly_stats``): Monday morning the admins get the
  last week in the moderation channel (or the bot log channel): members,
  new and gone, linked Discord accounts, events and training attendance.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select

from hubra.core.extensions import db

from . import api
from .api import DiscordError

DEFAULT_NAME = "👥 Mitglieder: {n}"
REASON = "Hubra: member count"


def _active():
    from hubra.core.models.member import Member

    return (
        Member.archived_at.is_(None),
        Member.deleted_at.is_(None),
        Member.status != "inactive",
    )


def member_count() -> int:
    from hubra.core.models.member import Member

    return int(db.session.scalar(select(func.count(Member.id)).where(*_active())) or 0)


def numbers(today: date | None = None) -> dict:
    """The last seven days, up to and including yesterday."""
    from hubra.core.models.connected_app import ExternalAccount
    from hubra.core.models.event import ClubEvent, EventAttendance
    from hubra.core.models.member import Member

    today = today or date.today()
    start = today - timedelta(days=7)
    since = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    members = member_count()
    joined = db.session.scalar(
        select(func.count(Member.id)).where(
            *_active(), Member.joined >= start, Member.joined < today
        )
    )
    left = db.session.scalar(
        select(func.count(Member.id)).where(Member.archived_at >= since)
    )
    linked = db.session.scalar(
        select(func.count(Member.id))
        .join(ExternalAccount, ExternalAccount.user_id == Member.user_id)
        .where(ExternalAccount.provider == "discord", *_active())
    )
    events = db.session.scalars(
        select(ClubEvent).where(ClubEvent.date >= start, ClubEvent.date < today)
    ).all()
    trainings = [e.id for e in events if e.category == "training"]
    present = 0
    if trainings:
        present = int(
            db.session.scalar(
                select(func.count(EventAttendance.id)).where(
                    EventAttendance.event_id.in_(trainings),
                    EventAttendance.status == "present",
                )
            )
            or 0
        )
    return {
        "members": members,
        "joined": int(joined or 0),
        "left": int(left or 0),
        "linked": int(linked or 0),
        "events": len(events) - len(trainings),
        "trainings": len(trainings),
        "present": present,
        "from": start,
        "to": today - timedelta(days=1),
    }


def weekly_embed(n: dict) -> dict:
    per = (
        f" (Ø {n['present'] / n['trainings']:.1f} pro Training)"
        if n["trainings"]
        else ""
    )
    lines = [
        f"👥 **{n['members']}** Mitglieder · +{n['joined']} neu · −{n['left']} ausgetreten",
        f"🔗 **{n['linked']}** mit Discord verknüpft"
        + (f" ({round(100 * n['linked'] / n['members'])} %)" if n["members"] else ""),
        f"📅 {n['events']} Anlässe · 🏃 {n['trainings']} Trainings, "
        f"{n['present']} Mal eingecheckt{per}",
    ]
    return {
        "title": f"📊 Woche {n['from']:%d.%m.} – {n['to']:%d.%m.%Y}",
        "description": "\n".join(lines),
        "color": 0x6D3FD8,
    }


# ── Stats channel ────────────────────────────────────────────────────────────


def name(conn, count: int | None = None) -> str:
    pattern = str((conn.config or {}).get("stats_name") or "").strip() or DEFAULT_NAME
    if "{n}" not in pattern:
        pattern = DEFAULT_NAME
    return pattern.replace("{n}", str(member_count() if count is None else count))[:100]


def _store(conn, channel_id: str | None) -> None:
    config = dict(conn.config or {})
    config["stats_channel_id"] = channel_id
    if channel_id:
        made = list(config.get("made_channels") or [])
        if channel_id not in made:
            config["made_channels"] = [*made, channel_id][-200:]
    conn.config = config


def ensure(conn) -> str:
    """Make, rename or delete the stats channel. Returns what was done.
    Raises ``DiscordError``."""
    from hubra.core import apps

    config = conn.config or {}
    stored = config.get("stats_channel_id")
    on = bool(config.get("stats_channel")) and apps.has_scope(conn, "channels")
    if not on:
        if not stored:
            return ""
        try:
            api.bot("DELETE", f"/channels/{stored}", reason=REASON)
        except DiscordError as exc:
            if exc.status != 404:
                raise
        _store(conn, None)
        return "stats channel deleted"
    wanted = name(conn)
    found = api.channel(stored) if stored else None
    if found is not None:
        if found.get("name") == wanted:
            return ""
        api.edit_channel(stored, {"name": wanted}, REASON)
        return f"stats channel: {wanted}"
    guild = str(conn.external_id)
    bot_id = str(api.config("client_id"))
    channel = api.create_channel(
        guild,
        {
            "name": wanted,
            "type": 2,  # voice: the name shows in the list, nobody writes
            "position": 0,
            "permission_overwrites": [
                {
                    "id": guild,
                    "type": 0,
                    "allow": str(api.VIEW_CHANNEL),
                    "deny": str(api.CONNECT),
                },
                {
                    "id": bot_id,
                    "type": 1,
                    "allow": str(api.VIEW_CHANNEL | api.MANAGE_CHANNELS),
                    "deny": "0",
                },
            ],
        },
        REASON,
    )
    _store(conn, str(channel["id"]))
    return f"stats channel made: {wanted}"
