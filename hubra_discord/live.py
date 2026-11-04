"""Messages that stay up to date after they are posted.

The bot remembers where it posted an event or a poll (``config["posted"]``:
``{"e:<event id>" | "p:<poll id>": [[channel id, message id], ...]}``) and
edits those messages later:

* an event shows the places taken after every sign-up or cancellation, its
  new date or place after a change, and "Abgesagt" without buttons once it
  is deleted;
* a poll, once closed (by hand, or because its closing time passed), shows
  its result without buttons, and a short "Umfrage beendet" goes to the
  same channel as a reply to it.

Only the newest entries are kept (``MAX_KEYS``), and an entry is dropped
once its event is over or its poll closed, so the list stays small.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from hubra.core.extensions import db

from . import api, botlog
from .api import DiscordError

_log = logging.getLogger(__name__)

KEY = "posted"
MAX_KEYS = 300
# A message per key: the main channel, a group channel, a reminder.
MAX_PER_KEY = 4


def event_key(event_id: int) -> str:
    return f"e:{event_id}"


def poll_key(poll_id: int) -> str:
    return f"p:{poll_id}"


def posted(conn) -> dict[str, list]:
    return dict((conn.config or {}).get(KEY) or {})


def remember(conn, key: str, channel_id: str, message_id: str) -> None:
    """Note a posted message.  The caller commits."""
    if not key or not channel_id or not message_id:
        return
    config = dict(conn.config or {})
    known = dict(config.get(KEY) or {})
    refs = [r for r in known.pop(key, []) if r[1] != str(message_id)]
    refs.append([str(channel_id), str(message_id)])
    known[key] = refs[-MAX_PER_KEY:]  # re-inserted: now the newest
    while len(known) > MAX_KEYS:
        known.pop(next(iter(known)))
    config[KEY] = known
    conn.config = config


def forget(conn, key: str) -> None:
    config = dict(conn.config or {})
    known = dict(config.get(KEY) or {})
    if known.pop(key, None) is not None:
        config[KEY] = known
        conn.config = config


def _edit(conn, channel_id: str, message_id: str, body: dict) -> bool:
    """Edit one message; False when it is gone (deleted on Discord)."""
    from hubra.core.tenant.context import current_tenant

    from .profile import brand

    body = brand(body, current_tenant(), conn.config or {})
    try:
        api.bot("PATCH", f"/channels/{channel_id}/messages/{message_id}", body)
    except DiscordError as exc:
        if exc.status == 404:
            return False
        raise
    return True


def _edit_all(conn, key: str, body: dict, what: str) -> int:
    """Edit every message of *key*; forgets the ones deleted on Discord.
    Returns how many were edited."""
    refs = posted(conn).get(key) or []
    kept, done = [], 0
    for channel_id, message_id in refs:
        try:
            if _edit(conn, channel_id, message_id, body):
                kept.append([channel_id, message_id])
                done += 1
        except DiscordError as exc:
            kept.append([channel_id, message_id])
            botlog.log(conn, "updated", False, f"{what}: {exc}")
    if len(kept) != len(refs):
        config = dict(conn.config or {})
        known = dict(config.get(KEY) or {})
        if kept:
            known[key] = kept
        else:
            known.pop(key, None)
        config[KEY] = known
        conn.config = config
    return done


# ── Events ───────────────────────────────────────────────────────────────────


def _over(event) -> bool:
    return (event.end_date or event.date) < date.today()


def refresh_event(conn, event_id: int, deleted: bool = False) -> int:
    """Bring the messages of one event up to date.  Returns how many."""
    from hubra.core.models.event import ClubEvent

    from .commands import event_buttons
    from .messages import event_embed

    key = event_key(event_id)
    if key not in posted(conn):
        return 0
    event = db.session.get(ClubEvent, event_id)
    if event is None:
        # Deleted: the message can no longer be built from the event, so
        # it keeps its text and only says so.
        done = _cancel_all(conn, key)
        forget(conn, key)
        return done
    state = "cancelled" if deleted else "over" if _over(event) else "open"
    body: dict = {"embeds": [event_embed(event, state=state)]}
    from hubra.core import apps

    body["components"] = (
        event_buttons(event)
        if state == "open" and apps.has_scope(conn, "commands")
        else []
    )
    done = _edit_all(conn, key, body, f'Event "{event.title}"')
    if state != "open":
        forget(conn, key)
    if done:
        botlog.log(conn, "updated", True, f'Event "{event.title}" ({state})')
    return done


def _cancel_all(conn, key: str) -> int:
    refs = posted(conn).get(key) or []
    done = 0
    for channel_id, message_id in refs:
        try:
            message = api.bot("GET", f"/channels/{channel_id}/messages/{message_id}")
        except DiscordError:
            continue
        embeds = [dict(e) for e in (message or {}).get("embeds") or []] or [{}]
        first = embeds[0]
        title = str(first.get("title") or "")
        if title.startswith("📅 "):
            title = title[2:]
        first["title"] = f"❌ Abgesagt: {title}"[:256]
        first["color"] = 0x8A8F98
        first["fields"] = [
            f for f in first.get("fields") or [] if not f["name"].startswith("👥")
        ]
        first["footer"] = {"text": "Dieser Anlass wurde abgesagt"}
        try:
            if _edit(
                conn, channel_id, message_id, {"embeds": embeds, "components": []}
            ):
                done += 1
        except DiscordError as exc:
            botlog.log(conn, "updated", False, f"Cancelled event: {exc}")
    if done:
        botlog.log(conn, "updated", True, "Event deleted: marked as cancelled")
    return done


# ── Polls ────────────────────────────────────────────────────────────────────


def _closed(poll) -> bool:
    from hubra.api.polls.services import is_closed

    return is_closed(poll)


def finish_poll(conn, poll_id: int) -> int:
    """A closed poll: its messages show the result, and the channel of the
    first one gets a short "Umfrage beendet" reply.  Returns how many
    messages were edited (0 while the poll is still open)."""
    from hubra.core.models.poll import Poll

    from .messages import poll_result_embed

    key = poll_key(poll_id)
    refs = posted(conn).get(key) or []
    if not refs:
        return 0
    poll = db.session.get(Poll, poll_id)
    if poll is None:
        forget(conn, key)
        return 0
    if not _closed(poll):
        return 0
    result = poll_result_embed(poll)
    done = _edit_all(conn, key, {"embeds": [result], "components": []}, "Poll result")
    forget(conn, key)
    if done:
        channel_id, message_id = refs[0]
        total = len(poll.votes or [])
        notice = {
            "content": (
                f"🗳️ **Umfrage beendet:** {poll.question[:200]} · "
                f"{total} Stimme{'n' if total != 1 else ''}"
            ),
            "allowed_mentions": {"parse": []},
            # Shown as a reply: one click to the result above.
            "message_reference": {
                "message_id": message_id,
                "fail_if_not_exists": False,
            },
        }
        try:
            try:
                api.send(channel_id, notice)
            except DiscordError as exc:
                # A reply needs Read Message History; without it, post plain.
                if exc.status != 403:
                    raise
                notice.pop("message_reference")
                api.send(channel_id, notice)
        except DiscordError as exc:
            botlog.log(conn, "announced", False, f"Poll closed notice: {exc}")
        botlog.log(
            conn, "updated", True, f'Poll "{poll.question}" closed: result shown'
        )
    return done


def due_polls(conn, now: datetime | None = None) -> list[int]:
    """Posted polls that may have closed: all of them, except those whose
    closing time is far off (a hand-closed poll is caught by poll.closed)."""
    from hubra.core.models.poll import Poll

    now = now or datetime.now(timezone.utc)
    ids = [int(k[2:]) for k in posted(conn) if k.startswith("p:") and k[2:].isdigit()]
    if not ids:
        return []
    rows = db.session.query(Poll).filter(Poll.id.in_(ids)).all()
    due = []
    for poll in rows:
        closes = poll.closes_at
        if closes is not None and closes.tzinfo is None:
            closes = closes.replace(tzinfo=timezone.utc)
        if poll.status == "closed" or (closes is not None and closes <= now):
            due.append(poll.id)
    # Polls deleted in the app: drop them.
    for poll_id in set(ids) - {p.id for p in rows}:
        forget(conn, poll_key(poll_id))
    return due


def over_events(conn) -> list[int]:
    """Posted events that are over: their buttons go."""
    from hubra.core.models.event import ClubEvent

    ids = [int(k[2:]) for k in posted(conn) if k.startswith("e:") and k[2:].isdigit()]
    if not ids:
        return []
    yesterday = date.today() - timedelta(days=1)
    rows = db.session.query(ClubEvent).filter(ClubEvent.id.in_(ids)).all()
    return [e.id for e in rows if (e.end_date or e.date) <= yesterday]
