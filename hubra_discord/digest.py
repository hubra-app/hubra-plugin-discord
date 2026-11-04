"""Monday morning posts.

* ``weekly_events``: the coming seven days' events in the events channel
  (or the main channel), one line each with a link; members-only events
  only where the club says the channel is for members.
* ``weekly_stats``: the last week's numbers for the admins (stats.py).

The hourly job (tasks.hourly) sends them once per week, from 07:00 club
time on Monday; ``weekly_sent`` holds the ISO week they went out.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from hubra.core.extensions import db

SEND_FROM_HOUR = 7
MAX_LINES = 15
WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


def _week(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def due(conn, now: datetime | None = None) -> bool:
    """Monday (or later in the week, if Monday was missed), not sent yet."""
    from hubra.core.tasks.reminder_tasks import club_zone

    config = conn.config or {}
    if not (config.get("weekly_events") or config.get("weekly_stats")):
        return False
    local = (now or datetime.now(timezone.utc)).astimezone(club_zone())
    if local.weekday() == 0 and local.hour < SEND_FROM_HOUR:
        return False
    return config.get("weekly_sent") != _week(local.date())


def mark_sent(conn, now: datetime | None = None) -> None:
    from hubra.core.tasks.reminder_tasks import club_zone

    local = (now or datetime.now(timezone.utc)).astimezone(club_zone())
    config = dict(conn.config or {})
    config["weekly_sent"] = _week(local.date())
    conn.config = config


def events_embed(conn, today: date | None = None) -> dict | None:
    """The next seven days' events, or None when there are none."""
    from hubra.core.models.event import ClubEvent

    from .messages import club_url

    today = today or date.today()
    members_ok = bool((conn.config or {}).get("members_only_events"))
    query = select(ClubEvent).where(
        ClubEvent.date >= today, ClubEvent.date < today + timedelta(days=7)
    )
    if not members_ok:
        query = query.where(ClubEvent.is_public.is_(True))
    events = db.session.scalars(query.order_by(ClubEvent.date, ClubEvent.time)).all()
    # Trainings have their own cards in the group channels.
    events = [e for e in events if e.category != "training"]
    if not events:
        return None
    lines = []
    for e in events[:MAX_LINES]:
        when = f"{WEEKDAYS[e.date.weekday()]} {e.date:%d.%m.}"
        if e.time and not e.all_day:
            when += f" {e.time}"
        link = club_url(f"/app/operations/events?event={e.id}")
        title = f"[{e.title}]({link})" if link else e.title
        where = f" · {e.location}" if e.location else ""
        lines.append(f"**{when}** · {title}{where}")
    if len(events) > MAX_LINES:
        lines.append(f"… und {len(events) - MAX_LINES} weitere")
    return {
        "title": "🗓️ Diese Woche im Verein",
        "description": "\n".join(lines)[:4000],
        "color": 0x6D3FD8,
        "footer": {"text": "Anmelden in der App oder bei den Anlässen"},
    }
