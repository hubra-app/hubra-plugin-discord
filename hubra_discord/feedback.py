"""Feedback after an event (``event_feedback``, ``commands`` scope).

A few hours after an event ends, the bot asks "Wie war «…»?" where the event
was announced (its group channel, else the events channel): buttons 1-5 and
"Etwas sagen" (a short form). Only members who signed up or were checked in
can answer; each person has one rating (a new click replaces it) and a few
words. Answers are anonymous: the club sees the average, the count and the
comments in Settings › Discord, not who gave them.

Trainings are left out (they come every week). Kept as ``feedback``:
{event id: {title, date, votes: {Discord user id: 1-5}, comments: [...]}},
the newest ``MAX_EVENTS`` only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

from hubra.core import apps
from hubra.core.extensions import db

ASK_AFTER = timedelta(hours=2)
ASK_UNTIL = timedelta(hours=36)
MAX_EVENTS = 30
MAX_COMMENTS = 50
MAX_COMMENT = 500
MAX_PER_RUN = 3


def _store(conn) -> dict:
    return dict((conn.config or {}).get("feedback") or {})


def _save(conn, store: dict) -> None:
    while len(store) > MAX_EVENTS:
        store.pop(next(iter(store)))
    config = dict(conn.config or {})
    config["feedback"] = store
    conn.config = config


def due(conn, now: datetime | None = None) -> list:
    """Events that ended a little while ago and weren't asked about yet."""
    from hubra.core.models.event import ClubEvent

    from .scheduled import times

    config = conn.config or {}
    if not config.get("event_feedback") or not apps.has_scope(conn, "commands"):
        return []
    now = now or datetime.now(timezone.utc)
    asked = _store(conn)
    rows = db.session.scalars(
        select(ClubEvent).where(
            ClubEvent.date >= (now - ASK_UNTIL - timedelta(days=1)).date(),
            ClubEvent.date <= now.date(),
            ClubEvent.category != "training",
        )
    ).all()
    out = []
    for event in rows:
        if str(event.id) in asked:
            continue
        if not event.is_public and not config.get("members_only_events"):
            from .groups import channel_for_group

            if channel_for_group(conn, event.group_id) is None:
                continue  # a members-only event, and only public channels
        _start, end = times(event)
        if end + ASK_AFTER <= now <= end + ASK_UNTIL and _took_part_any(event.id):
            out.append(event)
    return out[:MAX_PER_RUN]


def _took_part_any(event_id: int) -> bool:
    from hubra.core.models.event import EventAttendance, EventRegistration

    return bool(
        db.session.scalar(
            select(EventRegistration.id)
            .where(
                EventRegistration.event_id == event_id,
                EventRegistration.status.in_(("registered", "approved", "attended")),
            )
            .limit(1)
        )
        or db.session.scalar(
            select(EventAttendance.id)
            .where(
                EventAttendance.event_id == event_id,
                EventAttendance.status == "present",
            )
            .limit(1)
        )
    )


def took_part(event_id: int, member_id: int) -> bool:
    from hubra.core.models.event import EventAttendance, EventRegistration

    reg = db.session.scalar(
        select(EventRegistration.id).where(
            EventRegistration.event_id == event_id,
            EventRegistration.member_id == member_id,
            EventRegistration.status.in_(("registered", "approved", "attended")),
        )
    )
    if reg:
        return True
    return bool(
        db.session.scalar(
            select(EventAttendance.id).where(
                EventAttendance.event_id == event_id,
                EventAttendance.member_id == member_id,
                or_(EventAttendance.status == "present"),
            )
        )
    )


def where(conn, event) -> str | None:
    """The channel to ask in: the event's group channel, else where events go."""
    from .app import channel_for
    from .groups import channel_for_group

    return channel_for_group(conn, event.group_id) or channel_for(
        conn.config or {}, "public_events" if event.is_public else "events"
    )


def question(event) -> dict:
    stars = [
        {
            "type": 2,
            "style": 2,
            "label": str(n),
            "emoji": {"name": "⭐"} if n == 5 else None,
            "custom_id": f"fb:{event.id}:{n}",
        }
        for n in range(1, 6)
    ]
    for s in stars:
        if s["emoji"] is None:
            del s["emoji"]
    return {
        "embeds": [
            {
                "title": f"💬 Wie war «{event.title}»?"[:256],
                "description": (
                    "Gib 1 (schlecht) bis 5 (super), und wenn du magst ein paar "
                    "Worte. Anonym: der Verein sieht nur den Durchschnitt und die "
                    "Kommentare."
                ),
                "color": 0x6D3FD8,
            }
        ],
        "components": [
            {"type": 1, "components": stars},
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 1,
                        "label": "Etwas sagen",
                        "custom_id": f"fbc:{event.id}",
                    }
                ],
            },
        ],
        "allowed_mentions": {"parse": []},
    }


def mark_asked(conn, event) -> None:
    store = _store(conn)
    store[str(event.id)] = {
        "title": event.title,
        "date": event.date.isoformat(),
        "votes": {},
        "comments": [],
    }
    _save(conn, store)


def _check(conn, member, event_id: str):
    """(entry, error text): the event's feedback entry, if this member may answer."""
    from .commands import _link_hint

    if member is None:
        return None, _link_hint()
    entry = _store(conn).get(event_id)
    if entry is None:
        return None, "Für diesen Anlass nimmt Hubra keine Rückmeldungen mehr an."
    if not took_part(int(event_id), member.id):
        return None, "Rückmeldungen geben nur, die dabei waren."
    return entry, None


def vote(conn, member, discord_user_id: str, custom_id: str) -> dict:
    """``fb:<event>:<1-5>``."""
    from .commands import reply

    parts = custom_id.split(":")
    if len(parts) != 3 or not parts[1].isdigit() or parts[2] not in "12345":
        return reply("Diese Schaltfläche gilt nicht mehr.")
    entry, error = _check(conn, member, parts[1])
    if error:
        return reply(error)
    store = _store(conn)
    entry = dict(entry)
    votes = dict(entry.get("votes") or {})
    votes[str(discord_user_id)] = int(parts[2])
    entry["votes"] = votes
    store[parts[1]] = entry
    _save(conn, store)
    db.session.commit()
    return reply(
        f"Danke! Du hast {parts[2]} von 5 gegeben. «Etwas sagen» für ein paar Worte."
    )


def comment_form(conn, member, custom_id: str) -> dict:
    """``fbc:<event>``: the form for a few words."""
    from .commands import reply

    event_id = custom_id.partition(":")[2]
    if not event_id.isdigit():
        return reply("Diese Schaltfläche gilt nicht mehr.")
    entry, error = _check(conn, member, event_id)
    if error:
        return reply(error)
    return {
        "type": 9,
        "data": {
            "custom_id": f"fbt:{event_id}",
            "title": f"Rückmeldung: {entry.get('title', '')}"[:45],
            "components": [
                {
                    "type": 1,
                    "components": [
                        {
                            "type": 4,
                            "custom_id": "text",
                            "label": "Was war gut, was nicht?",
                            "style": 2,
                            "min_length": 2,
                            "max_length": MAX_COMMENT,
                            "required": True,
                        }
                    ],
                }
            ],
        },
    }


def comment_sent(conn, member, interaction: dict) -> dict:
    """``fbt:<event>``: the form came back."""
    from .commands import reply
    from .mod import _field

    custom_id = str((interaction.get("data") or {}).get("custom_id", ""))
    event_id = custom_id.partition(":")[2]
    entry, error = _check(conn, member, event_id)
    if error:
        return reply(error)
    text = " ".join(_field(interaction, "text").split())[:MAX_COMMENT]
    if not text:
        return reply("Kein Text angekommen.")
    store = _store(conn)
    entry = dict(entry)
    comments = list(entry.get("comments") or [])
    comments.append({"text": text, "at": datetime.now(timezone.utc).isoformat()})
    entry["comments"] = comments[-MAX_COMMENTS:]
    store[event_id] = entry
    _save(conn, store)
    db.session.commit()
    return reply("Danke für deine Rückmeldung!")


def results(conn) -> list[dict]:
    """For the settings page: newest first, without who answered."""
    out = []
    for event_id, entry in reversed(list(_store(conn).items())):
        votes = list((entry.get("votes") or {}).values())
        out.append(
            {
                "eventId": int(event_id),
                "title": entry.get("title", ""),
                "date": entry.get("date"),
                "count": len(votes),
                "average": round(sum(votes) / len(votes), 1) if votes else None,
                "spread": [votes.count(n) for n in range(1, 6)],
                "comments": [c.get("text", "") for c in entry.get("comments") or []],
            }
        )
    return out
