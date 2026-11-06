"""The club's events as Discord events (the server's Events list).

The bot keeps the server's scheduled events in step with the club's events
for the next ``HORIZON_DAYS``: it creates them, changes them when the event
changes and deletes them when the event is deleted or no longer fits. Only
events the whole server may see go there: public ones, and members-only ones
when the club says the server is for members (``members_only_events``).
Events of one group stay out (their group channel announces them).

Which Discord event belongs to which Hubra event is kept in the connection
config (``scheduled_events``: ``{event id: [discord id, fingerprint]}``), so
Discord is only called for what changed.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select

from hubra.core.extensions import db

from . import api

HORIZON_DAYS = 60
# Discord allows 100 scheduled events per server; leave room for the club's own.
MAX_EVENTS = 50
EXTERNAL = 3
GUILD_ONLY = 2


def _zone():
    from hubra.core.tasks.reminder_tasks import club_zone

    return club_zone()


# An event's time field, as Hubra reads it ("19:00" or "19:00-21:00").
# Kept here: it is only reading the row, and a deleted event still has one.
_TIME_RANGE = re.compile(r"^\s*(\d{1,2})[:.](\d{2})\s*(?:-\s*(\d{1,2})[:.](\d{2}))?")


def _event_times(event):
    """Local start and end datetimes, or (None, None) for all-day events."""
    if event.all_day or not event.time:
        return None, None
    m = _TIME_RANGE.match(event.time)
    if not m:
        return None, None
    try:
        start = datetime.combine(event.date, time(int(m[1]), int(m[2])))
        if m[3]:
            end = datetime.combine(
                event.end_date or event.date, time(int(m[3]), int(m[4]))
            )
        else:
            end = start + timedelta(hours=1)
    except ValueError:
        return None, None
    if end <= start:
        end = start + timedelta(hours=1)
    return start, end


def times(event) -> tuple[datetime, datetime]:
    """Start and end in UTC. All-day events run from midnight to midnight
    (club time)."""
    zone = _zone()
    start, end = _event_times(event)
    if start is None:
        start = datetime.combine(event.date, time(0, 0))
        end = datetime.combine(
            (event.end_date or event.date) + timedelta(days=1), time(0, 0)
        )
    return (
        start.replace(tzinfo=zone).astimezone(timezone.utc),
        end.replace(tzinfo=zone).astimezone(timezone.utc),
    )


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def body(event) -> dict:
    from .messages import club_url

    start, end = times(event)
    where = event.location or event.meeting_url or ""
    if not where:
        from .profile import club_name
        from hubra.core.tenant.context import current_tenant

        where = club_name(current_tenant()) or "Hubra"
    link = club_url(f"/app/operations/events?event={event.id}") or ""
    text = _clip(event.description or "", 1000 - len(link) - 2)
    description = f"{text}\n\n{link}".strip() if link else text
    return {
        "name": _clip(event.title, 100),
        "privacy_level": GUILD_ONLY,
        "entity_type": EXTERNAL,
        "entity_metadata": {"location": _clip(where, 100)},
        "scheduled_start_time": start.isoformat(),
        "scheduled_end_time": end.isoformat(),
        "description": description[:1000],
    }


def fingerprint(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


def wanted(conn) -> list:
    """The club's events that belong on the server, soonest first."""
    from hubra.core.models.event import ClubEvent

    today = date.today()
    query = select(ClubEvent).where(
        ClubEvent.date >= today,
        ClubEvent.date <= today + timedelta(days=HORIZON_DAYS),
        ClubEvent.group_id.is_(None),
    )
    if not (conn.config or {}).get("members_only_events"):
        query = query.where(ClubEvent.is_public.is_(True))
    events = db.session.scalars(
        query.order_by(ClubEvent.date, ClubEvent.time, ClubEvent.id).limit(MAX_EVENTS)
    ).all()
    return list(events)


def sync(conn) -> dict:
    """Create, change and delete the server's events. Returns counts.

    Raises ``api.DiscordError`` for the caller to record or retry; what was
    done before the error is kept."""
    config = dict(conn.config or {})
    known: dict[str, list] = dict(config.get("scheduled_events") or {})
    guild = conn.external_id
    now = datetime.now(timezone.utc)
    counts = {"created": 0, "changed": 0, "deleted": 0}
    events = wanted(conn) if config.get("discord_events", True) else []

    def save():
        config["scheduled_events"] = dict(known)
        conn.config = dict(config)

    try:
        keep = set()
        for event in events:
            data = body(event)
            fp = fingerprint(data)
            key = str(event.id)
            start = datetime.fromisoformat(data["scheduled_start_time"])
            entry = known.get(key)
            if entry is None:
                if start <= now:
                    continue  # Discord only takes events that are still ahead
                created = api.create_event(guild, data)
                known[key] = [str(created["id"]), fp]
                counts["created"] += 1
            elif entry[1] != fp and start > now:
                try:
                    api.edit_event(guild, entry[0], data)
                    known[key] = [entry[0], fp]
                except api.DiscordError as exc:
                    if exc.status != 404:
                        raise
                    created = api.create_event(guild, data)  # deleted on Discord
                    known[key] = [str(created["id"]), fp]
                counts["changed"] += 1
            keep.add(key)

        for key in list(known):
            if key in keep:
                continue
            discord_id = known[key][0]
            still_ahead = _ahead(key, now)
            if still_ahead:
                api.delete_event(guild, discord_id)
                counts["deleted"] += 1
            del known[key]  # past ones end on their own
    finally:
        save()
    if config.get("discord_events", True) and not config.get("members_only_events"):
        # Said in the log, so "my event is missing" has an answer.
        counts["members-only left out"] = _members_only_ahead()
    return counts


def _members_only_ahead() -> int:
    from sqlalchemy import func

    from hubra.core.models.event import ClubEvent

    today = date.today()
    return (
        db.session.scalar(
            select(func.count(ClubEvent.id)).where(
                ClubEvent.date >= today,
                ClubEvent.date <= today + timedelta(days=HORIZON_DAYS),
                ClubEvent.group_id.is_(None),
                ClubEvent.is_public.is_(False),
            )
        )
        or 0
    )


def _ahead(key: str, now: datetime) -> bool:
    """Whether the Hubra event *key* is gone or hasn't started yet (so its
    Discord event should be deleted rather than left to end)."""
    from hubra.core.models.event import ClubEvent

    try:
        event = db.session.get(ClubEvent, int(key))
    except ValueError:
        return False
    if event is None:
        return True
    start, _ = times(event)
    return start > now


def remove_all(conn) -> int:
    """Delete every event the bot made (the club turned it off)."""
    config = dict(conn.config or {})
    known = dict(config.get("scheduled_events") or {})
    now = datetime.now(timezone.utc)
    deleted = 0
    for key, entry in list(known.items()):
        if _ahead(key, now):
            api.delete_event(conn.external_id, entry[0])
            deleted += 1
        del known[key]
    config["scheduled_events"] = known
    conn.config = config
    return deleted
