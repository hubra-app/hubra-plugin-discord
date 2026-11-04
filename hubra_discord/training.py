"""Training cards in a group's channel.

When the reminder for a training is due (the club's reminder time, 24 hours
before by default) and the training's group has its own channel, the bot
posts a card there instead of the plain reminder: when and where, how many
checked in or excused themselves so far, and "Bin da" / "Kann nicht" /
"Zurücknehmen". A click goes through the same rules as /trainings
(personal.attend): only who belongs to the training, check-in from an hour
before, excusing until it starts. The card's count is updated after every
click from Discord, and once the training is over the buttons go and the
final count stays (tidy_messages).

Remembered like other live messages, as ``t:<event id>`` (live.py).
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select

from hubra.core.extensions import db

from . import live

PREFIX = "t:"


def key(event_id: int) -> str:
    return f"{PREFIX}{event_id}"


def wanted(conn, event) -> bool:
    """A card for this training on this server?"""
    from .groups import channel_for_group

    return (
        event is not None
        and event.category == "training"
        and event.group_id is not None
        and bool((conn.config or {}).get("training_posts", True))
        and channel_for_group(conn, event.group_id) is not None
    )


def counts(event_id: int) -> dict[str, int]:
    from hubra.core.models.event import EventAttendance

    rows = db.session.execute(
        select(EventAttendance.status, func.count(EventAttendance.id))
        .where(EventAttendance.event_id == event_id)
        .group_by(EventAttendance.status)
    ).all()
    return {str(status): int(n) for status, n in rows}


def _buttons(event_id: int) -> list[dict]:
    return [
        {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 3,
                    "label": "Bin da",
                    "custom_id": f"tatt:{event_id}:present",
                    "emoji": {"name": "✅"},
                },
                {
                    "type": 2,
                    "style": 2,
                    "label": "Kann nicht",
                    "custom_id": f"tatt:{event_id}:excused",
                    "emoji": {"name": "🙅"},
                },
                {
                    "type": 2,
                    "style": 2,
                    "label": "Zurücknehmen",
                    "custom_id": f"tatt:{event_id}:clear",
                },
            ],
        }
    ]


def card(event, *, over: bool = False) -> tuple[dict, list | None]:
    """The card's embed and its buttons (none once it is over)."""
    from .messages import event_embed

    item = event_embed(event, state="over" if over else "open")
    item["title"] = f"🏃 Training: {event.title}"[:256]
    n = counts(event.id)
    line = f"✅ {n.get('present', 0)} da · 🙅 {n.get('excused', 0)} entschuldigt"
    if n.get("absent"):
        line += f" · ❌ {n['absent']} abwesend"
    # Places don't matter for a training; the count does.
    fields = [f for f in item.get("fields", []) if not f["name"].startswith("👥")]
    fields.append({"name": "Teilnahme", "value": line, "inline": False})
    item["fields"] = fields
    item["footer"] = {
        "text": (
            "Vorbei"
            if over
            else "«Bin da» ab einer Stunde vor Beginn, «Kann nicht» bis zum Beginn"
        )
    }
    return item, None if over else _buttons(event.id)


def body(event, *, over: bool = False) -> dict:
    from .messages import payload

    item, components = card(event, over=over)
    return payload(None, [item], components)


def refresh(conn, event_id: int) -> int:
    """Bring the training's cards up to date.  Returns how many."""
    from hubra.core.models.event import ClubEvent

    k = key(event_id)
    if k not in live.posted(conn):
        return 0
    event = db.session.get(ClubEvent, event_id)
    if event is None:
        live.forget(conn, k)
        return 0
    over = (event.end_date or event.date) < date.today()
    done = live._edit_all(conn, k, body(event, over=over), f'Training "{event.title}"')
    if over:
        live.forget(conn, k)
    return done


def over(conn) -> list[int]:
    """Posted trainings that are over: the last refresh drops the buttons."""
    from hubra.core.models.event import ClubEvent

    ids = [
        int(k[len(PREFIX) :])
        for k in live.posted(conn)
        if k.startswith(PREFIX) and k[len(PREFIX) :].isdigit()
    ]
    if not ids:
        return []
    yesterday = date.today() - timedelta(days=1)
    rows = db.session.scalars(select(ClubEvent).where(ClubEvent.id.in_(ids))).all()
    gone = set(ids) - {e.id for e in rows}
    for event_id in gone:
        live.forget(conn, key(event_id))
    return [e.id for e in rows if (e.end_date or e.date) <= yesterday]


def click(conn, member, custom_id: str) -> dict:
    """``tatt:<event>:present|excused|clear`` from a card."""
    from .personal import attend
    from .tasks import refresh_training

    _, _, rest = custom_id.partition(":")
    answer = attend(conn, member, f"attend:{rest}")
    event_id = rest.partition(":")[0]
    if event_id.isdigit() and key(int(event_id)) in live.posted(conn):
        # attend() committed already; the card follows in the background.
        refresh_training.delay(conn.id, int(event_id))
    return answer
