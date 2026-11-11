"""Doing club work from Discord: /anlass, /umfrage, /beschluss, /gruppen, /hilfe.

Each command works like the same step in the app and needs the same right
(``events.events.create``, ``polls.polls.create``); the person must have
linked their Discord account to a member.  What they make is announced as
usual (hooks.py) and shows in the app at once.  Answers are only visible to
the person.

* ``/anlass titel datum [zeit] [ort] [beschreibung] [oeffentlich]``
* ``/umfrage frage optionen [schliesst]``: options separated by ``;``
* ``/beschluss frage funktion [tage]``: a circular resolution for the
  holders of one function: Ja / Nein / Enthaltung, required, closes once
  everyone has voted (``auto_close``) or after *tage* days.
* ``/gruppen``: the open groups, with buttons to join or leave.
* ``/hilfe``: what the bot can do here.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from hubra.core.errors import ApiError
from hubra.core.extensions import db

from . import botlog

ZONE = ZoneInfo("Europe/Zurich")
STRING, INTEGER, BOOLEAN = 3, 4, 5
MAX_POLL_OPTIONS = 10
RESOLUTION_DAYS = 7


def _opt(name, en, description, en_description, kind=STRING, required=False, **extra):
    return {
        "name": name,
        "name_localizations": {"en-GB": en, "en-US": en},
        "description": description,
        "description_localizations": {"en-GB": en_description, "en-US": en_description},
        "type": kind,
        "required": required,
        **extra,
    }


def _command(name, en, description, en_description, options=()):
    return {
        "name": name,
        "name_localizations": {"en-GB": en, "en-US": en},
        "description": description,
        "description_localizations": {"en-GB": en_description, "en-US": en_description},
        "type": 1,
        "contexts": [0],
        "options": list(options),
    }


COMMANDS = [
    _command(
        "anlass",
        "new-event",
        "Einen Anlass im Verein erstellen",
        "Create a club event",
        [
            _opt(
                "titel",
                "title",
                "Wie der Anlass heisst",
                "The event's name",
                required=True,
                max_length=255,
            ),
            _opt(
                "datum",
                "date",
                "TT.MM.JJJJ, heute oder morgen",
                "DD.MM.YYYY, today or tomorrow",
                required=True,
            ),
            _opt(
                "zeit",
                "time",
                "z. B. 19:00 oder 19:00-21:00",
                "e.g. 19:00 or 19:00-21:00",
            ),
            _opt("ort", "place", "Wo", "Where", max_length=255),
            _opt(
                "beschreibung",
                "description",
                "Worum es geht",
                "What it is about",
                max_length=2000,
            ),
            _opt(
                "oeffentlich",
                "public",
                "Auch für Nicht-Mitglieder sichtbar",
                "Visible to non-members too",
                BOOLEAN,
            ),
        ],
    ),
    _command(
        "umfrage",
        "new-poll",
        "Eine Umfrage im Verein starten",
        "Start a club poll",
        [
            _opt(
                "frage",
                "question",
                "Die Frage",
                "The question",
                required=True,
                max_length=255,
            ),
            _opt(
                "optionen",
                "options",
                "Antworten, getrennt mit ; (z. B. Ja; Nein; Egal)",
                "Answers separated by ; (e.g. Yes; No; Either)",
                required=True,
                max_length=1000,
            ),
            _opt("schliesst", "closes", "TT.MM.JJJJ [HH:MM]", "DD.MM.YYYY [HH:MM]"),
        ],
    ),
    _command(
        "beschluss",
        "resolution",
        "Umlaufbeschluss: Ja/Nein/Enthaltung für eine Funktion",
        "Circular resolution: yes/no/abstain for one function",
        [
            _opt(
                "frage",
                "question",
                "Worüber entschieden wird",
                "What is decided",
                required=True,
                max_length=255,
            ),
            _opt(
                "funktion",
                "function",
                "Wer abstimmt (z. B. Vorstand)",
                "Who votes (e.g. the board)",
                required=True,
                autocomplete=True,
            ),
            _opt(
                "tage",
                "days",
                "Spätestens nach so vielen Tagen schliessen (Standard 7)",
                "Close after this many days at the latest (default 7)",
                INTEGER,
                min_value=1,
                max_value=60,
            ),
        ],
    ),
    _command(
        "gruppen",
        "groups",
        "Offene Gruppen: beitreten oder austreten",
        "Open groups: join or leave",
    ),
    _command(
        "hilfe", "help", "Was der Hubra-Bot hier kann", "What the Hubra bot can do here"
    ),
]
NAMES = {c["name"] for c in COMMANDS}


def _options(data: dict) -> dict:
    return {o["name"]: o.get("value") for o in data.get("options") or []}


# ── Dates and times as people type them ──────────────────────────────────────


def parse_date(text: str, today: date | None = None) -> date | None:
    text = (text or "").strip().lower()
    today = today or datetime.now(ZONE).date()
    if text in ("heute", "today"):
        return today
    if text in ("morgen", "tomorrow"):
        return today + timedelta(days=1)
    if text in ("übermorgen", "uebermorgen"):
        return today + timedelta(days=2)
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})?", text)
    if m:
        year = m.group(3)
        y = today.year if not year else int(year) + (2000 if len(year) == 2 else 0)
        try:
            found = date(y, int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
        if not year and found < today:  # "3.1." in December: next year
            found = found.replace(year=y + 1)
        return found
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


_TIME = r"([01]?\d|2[0-3])[:.]([0-5]\d)"


def parse_time(text: str | None) -> str | None:
    """'19:00' or '19:00-21:00' as the app stores it; '' for none, None
    for something unreadable."""
    text = (text or "").strip()
    if not text:
        return ""
    m = re.fullmatch(rf"{_TIME}(?:\s*[-–]\s*{_TIME})?", text)
    if not m:
        return None
    start = f"{int(m.group(1)):02d}:{m.group(2)}"
    if m.group(3):
        return f"{start}-{int(m.group(3)):02d}:{m.group(4)}"
    return start


def parse_closing(text: str | None) -> datetime | None | bool:
    """A closing time (end of the day if no time is given); None for none,
    False for something unreadable."""
    text = (text or "").strip()
    if not text:
        return None
    day_text, _, time_text = text.partition(" ")
    day = parse_date(day_text)
    if day is None:
        return False
    at = time(23, 59)
    if time_text.strip():
        m = re.fullmatch(_TIME, time_text.strip())
        if not m:
            return False
        at = time(int(m.group(1)), int(m.group(2)))
    return datetime.combine(day, at, tzinfo=ZONE)


# ── The commands ─────────────────────────────────────────────────────────────


def handle(conn, member, name: str, data: dict) -> dict:
    from .commands import _link_hint, _may, reply

    if name == "hilfe":
        return help_text(conn, member)
    if member is None:
        return reply(_link_hint())
    if name == "gruppen":
        return groups(member)
    if name == "anlass":
        if not _may(member, "events.events.create"):
            return reply("Du darfst keine Anlässe erstellen.")
        return new_event(conn, member, _options(data))
    if not _may(member, "polls.polls.create"):
        return reply("Du darfst keine Umfragen starten.")
    if name == "umfrage":
        return new_poll(conn, member, _options(data))
    return new_resolution(conn, member, _options(data))


def _failed(exc: ApiError) -> str:
    return f"Das ging nicht: {exc.message}"


def new_event(conn, member, opts: dict) -> dict:
    from hubra.core.contracts import SYSTEM, call

    from .commands import reply
    from .messages import club_url

    day = parse_date(str(opts.get("datum") or ""))
    if day is None:
        return reply(
            "Das Datum verstehe ich nicht. Schreib es als TT.MM.JJJJ, heute oder morgen."
        )
    if day < datetime.now(ZONE).date():
        return reply("Das Datum liegt in der Vergangenheit.")
    at = parse_time(opts.get("zeit"))
    if at is None:
        return reply(
            "Die Zeit verstehe ich nicht. Schreib sie als 19:00 oder 19:00-21:00."
        )
    body = {
        "title": str(opts.get("titel") or "").strip(),
        "date": day.isoformat(),
        "time": at or None,
        "all_day": not at,
        "location": (str(opts.get("ort") or "").strip() or None),
        "description": (str(opts.get("beschreibung") or "").strip() or None),
        "is_public": bool(opts.get("oeffentlich")),
        "type": "Social",
    }
    try:
        # Checked like the app's event form; the command checked the right.
        event = call(
            "events.event.create", body, actor_id=member.id, actor=SYSTEM
        )
        db.session.commit()
    except ApiError as exc:  # a Refused too: VALIDATION_ERROR, ...
        db.session.rollback()
        return reply(_failed(exc))
    botlog.log(conn, "command", True, f"/anlass by {member.name}: {event['title']}")
    db.session.commit()
    link = club_url(f"/app/operations/events?event={event['id']}")
    day = date.fromisoformat(event["date"])
    text = f"✅ **{event['title']}** am {day:%d.%m.%Y} ist erstellt."
    if link:
        text += f"\nIn der App ergänzen (Plätze, Anmeldeschluss, Bild): {link}"
    return reply(text)


def _poll(conn, member, body: dict, what: str) -> dict:
    from hubra.core.contracts import SYSTEM, call

    from .commands import reply
    from .messages import club_url

    try:
        # Checked like the app's poll form; the command checked the right.
        poll = call("polls.poll.create", body, actor_id=member.id, actor=SYSTEM)
        db.session.commit()
    except ApiError as exc:  # a Refused too: VALIDATION_ERROR, ...
        db.session.rollback()
        return reply(_failed(exc))
    botlog.log(conn, "command", True, f"/{what} by {member.name}: {poll['question']}")
    db.session.commit()
    link = club_url(f"/app/operations/polls?poll={poll['id']}")
    text = f"✅ **{poll['question']}** ist gestartet."
    if link:
        text += f"\n{link}"
    return reply(text)


def new_poll(conn, member, opts: dict) -> dict:
    from .commands import reply

    answers = [a.strip() for a in str(opts.get("optionen") or "").split(";")]
    answers = [a[:255] for a in answers if a]
    if len(answers) < 2:
        return reply(
            "Gib mindestens zwei Antworten an, getrennt mit ; (z. B. Ja; Nein)."
        )
    if len(answers) > MAX_POLL_OPTIONS:
        return reply(f"Höchstens {MAX_POLL_OPTIONS} Antworten; für mehr nimm die App.")
    closes = parse_closing(opts.get("schliesst"))
    if closes is False:
        return reply(
            "Das Enddatum verstehe ich nicht. Schreib es als TT.MM.JJJJ oder TT.MM.JJJJ HH:MM."
        )
    body = {
        "question": str(opts.get("frage") or "").strip(),
        "options": [{"label": a} for a in answers],
        "closes_at": closes.isoformat() if closes else None,
    }
    return _poll(conn, member, body, "umfrage")


def new_resolution(conn, member, opts: dict) -> dict:
    from hubra.core.models.function import Function

    from .commands import reply

    raw = str(opts.get("funktion") or "")
    function = db.session.get(Function, int(raw)) if raw.isdigit() else None
    if function is None:
        # The exact name (any case); not ilike, where % or _ in what someone
        # typed would match some other function.
        function = db.session.scalar(
            select(Function)
            .where(db.func.lower(Function.name) == raw.strip().lower())
            .limit(1)
        )
    if function is None:
        return reply(
            "Diese Funktion gibt es im Verein nicht. Wähle eine aus der Liste."
        )
    if not function.holders:
        return reply(f"«{function.name}» hat niemand inne; so kann niemand abstimmen.")
    days = int(opts.get("tage") or RESOLUTION_DAYS)
    closes = datetime.now(ZONE) + timedelta(days=max(1, min(days, 60)))
    body = {
        "question": str(opts.get("frage") or "").strip(),
        "options": [{"label": "Ja"}, {"label": "Nein"}, {"label": "Enthaltung"}],
        "closes_at": closes.isoformat(),
        "is_required": True,
        "required_function_ids": [function.id],
    }
    from hubra.core.schemas.poll import PollCreateSchema

    if "auto_close" in PollCreateSchema().fields:  # Hubra with circular resolutions
        body["auto_close"] = True
    return _poll(conn, member, body, "beschluss")


def autocomplete(conn, data: dict) -> dict:
    """Choices while someone types the function for /beschluss."""
    from hubra.core.models.function import Function

    typed = ""
    for o in data.get("options") or []:
        if o.get("focused"):
            typed = str(o.get("value") or "")
    query = select(Function).order_by(Function.name).limit(25)
    if typed:
        query = query.where(Function.name.ilike(f"%{typed}%"))
    return {
        "type": 8,
        "data": {
            "choices": [
                {"name": f.name[:100], "value": str(f.id)}
                for f in db.session.scalars(query)
            ]
        },
    }


# ── Groups ───────────────────────────────────────────────────────────────────


def _group_rows(entries: list[dict]) -> list[dict]:
    buttons = [
        {
            "type": 2,
            "style": 2 if g["joined"] else 1,
            "label": (("✓ " if g["joined"] else "") + g["name"])[:80],
            "custom_id": f"{'gleave' if g['joined'] else 'gjoin'}:{g['id']}",
        }
        for g in entries[:25]
    ]
    return [
        {"type": 1, "components": buttons[i : i + 5]} for i in range(0, len(buttons), 5)
    ]


def groups(member, note: str = "") -> dict:
    from hubra.core.contracts import SYSTEM, query

    from .commands import EPHEMERAL

    entries = query("groups.self_join.list", member_id=member.id, actor=SYSTEM)
    if not entries:
        return {
            "type": 4,
            "data": {
                "content": "Es gibt gerade keine offenen Gruppen.",
                "flags": EPHEMERAL,
            },
        }
    lines = [note] if note else []
    lines.append("**Offene Gruppen** · tippe zum Beitreten, ✓ zum Austreten")
    for g in entries[:25]:
        desc = f": {g['description'][:80]}" if g.get("description") else ""
        mark = "✓ " if g["joined"] else ""
        lines.append(f"{mark}**{g['name']}** ({g['member_count']}){desc}")
    return {
        "type": 4,
        "data": {
            "content": "\n".join(lines)[:2000],
            "flags": EPHEMERAL,
            "components": _group_rows(entries),
            "allowed_mentions": {"parse": []},
        },
    }


def group_button(conn, member, custom_id: str) -> dict:
    """gjoin:<id> / gleave:<id>: answer by updating the same message."""
    from hubra.core.contracts import SYSTEM, call
    from hubra.core.models.group import Group

    from .commands import _link_hint, reply

    if member is None:
        return reply(_link_hint())
    action, _, raw = custom_id.partition(":")
    if not raw.isdigit():
        return reply("Diese Schaltfläche gilt nicht mehr.")
    group = db.session.get(Group, int(raw))
    try:
        joined = action == "gjoin"
        call(
            "groups.self_join.set",
            group_id=int(raw),
            member_id=member.id,
            joined=joined,
            actor=SYSTEM,
        )
        if joined:
            note = f"✅ Du bist jetzt in **{group.name if group else raw}**."
        else:
            note = f"Du bist aus **{group.name if group else raw}** ausgetreten."
        db.session.commit()
    except ApiError as exc:
        db.session.rollback()
        note = _failed(exc)
    botlog.log(
        conn,
        "command",
        True,
        f"{action} by {member.name}: {group.name if group else raw}",
    )
    db.session.commit()
    answer = groups(member, note)
    answer["type"] = 7  # update the message the button is on
    return answer


# ── Help ─────────────────────────────────────────────────────────────────────


def help_text(conn, member) -> dict:
    from hubra.core import apps

    from .commands import _link_hint, _may, reply

    lines = ["**Was der Hubra-Bot hier kann**"]
    if apps.has_scope(conn, "commands"):
        lines += [
            "`/events`: die nächsten Anlässe",
            "`/trainings`: deine Trainings, abmelden und einchecken",
            "`/rechnungen`: deine offenen Rechnungen",
            "`/profil`: deine Mitgliedschaft",
            "`/gruppen`: offenen Gruppen beitreten",
        ]
        if member is not None and _may(member, "events.events.create"):
            lines.append("`/anlass`: einen Anlass erstellen")
        if member is not None and _may(member, "polls.polls.create"):
            lines.append(
                "`/umfrage`, `/beschluss`: Umfrage oder Umlaufbeschluss starten"
            )
        lines.append("Unter Anlässen und Umfragen: Knöpfe zum Anmelden und Abstimmen.")
    else:
        lines.append("`/events`: die nächsten Anlässe")
    if member is None:
        lines.append("")
        lines.append(_link_hint())
    else:
        lines.append("`/verbinden`: dein Konto ist verknüpft ✅")
    return reply("\n".join(lines))
