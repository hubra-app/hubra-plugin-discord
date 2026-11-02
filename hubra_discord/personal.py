"""Personal commands: /rechnungen, /trainings, /profil.

Every answer is only visible to the person who asked, and only for a linked
member of the server's club. Training check-in follows the club's setting
(``self_checkin``): from an hour before the start until the end of that day
a member marks themselves present; before the start they can say they can't
come (excused). A trainer's entry on the roll is the same record and can be
changed by them as usual.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select


from . import botlog
from hubra.core.extensions import db

UPCOMING_DAYS = 14
CHECKIN_BEFORE = timedelta(hours=1)

COMMANDS = [
    {
        "name": "rechnungen",
        "name_localizations": {"en-GB": "invoices", "en-US": "invoices"},
        "description": "Deine offenen Rechnungen",
        "description_localizations": {
            "en-GB": "Your open invoices",
            "en-US": "Your open invoices",
        },
        "type": 1,
        "contexts": [0],
    },
    {
        "name": "trainings",
        "description": "Deine nächsten Trainings, mit Abmelden und Einchecken",
        "description_localizations": {
            "en-GB": "Your next trainings, to excuse yourself or check in",
            "en-US": "Your next trainings, to excuse yourself or check in",
        },
        "type": 1,
        "contexts": [0],
    },
    {
        "name": "profil",
        "name_localizations": {"en-GB": "profile", "en-US": "profile"},
        "description": "Deine Mitgliedschaft im Verein",
        "description_localizations": {
            "en-GB": "Your membership in the club",
            "en-US": "Your membership in the club",
        },
        "type": 1,
        "contexts": [0],
    },
]


def _money(amount) -> str:
    return f"{amount:,.2f}".replace(",", "'")


def invoices(member) -> dict:
    from hubra.core.models.finance import Invoice

    from .commands import reply
    from .messages import club_url

    rows = db.session.scalars(
        select(Invoice)
        .where(Invoice.member_id == member.id, Invoice.status.in_(("Open", "Overdue")))
        .order_by(Invoice.due, Invoice.id)
        .limit(10)
    ).all()
    if not rows:
        return reply("✅ Du hast keine offenen Rechnungen.")
    today = date.today()
    lines = []
    for inv in rows:
        late = inv.status == "Overdue" or inv.due < today
        what = inv.description or (
            "Mitgliederbeitrag" if inv.purpose == "membership" else "Rechnung"
        )
        lines.append(
            f"{'⚠️' if late else '•'} **{_money(inv.amount)}** · {what} · "
            f"fällig {inv.due:%d.%m.%Y}{' (überfällig)' if late else ''}"
        )
    link = club_url("/app/dashboard")
    if link:
        lines.append(f"\nQR-Rechnung zum Bezahlen: {link}")
    return reply("\n".join(lines))


# ── Trainings ────────────────────────────────────────────────────────────────


def _expected_events(member) -> list:
    """The member's coming trainings: events of their groups, and events
    they hold a place at, in the next ``UPCOMING_DAYS``."""
    from hubra.core.models.event import ClubEvent, EventRegistration
    from hubra.core.models.member import member_groups

    today = date.today()
    groups = set(
        db.session.scalars(
            select(member_groups.c.group_id).where(
                member_groups.c.member_id == member.id
            )
        ).all()
    )
    signed_up = set(
        db.session.scalars(
            select(EventRegistration.event_id).where(
                EventRegistration.member_id == member.id,
                EventRegistration.status.in_(("registered", "approved", "attended")),
            )
        ).all()
    )
    query = select(ClubEvent).where(
        ClubEvent.date >= today,
        ClubEvent.date <= today + timedelta(days=UPCOMING_DAYS),
        ClubEvent.category == "training",
    )
    events = db.session.scalars(query.order_by(ClubEvent.date, ClubEvent.time)).all()
    return [e for e in events if e.group_id in groups or e.id in signed_up][:5]


def _window(event) -> tuple[datetime, datetime]:
    """When check-in opens (an hour before the start) and when the day ends."""
    from .scheduled import times

    start, _end = times(event)
    from hubra.core.tasks.reminder_tasks import club_zone

    zone = club_zone()
    day_end = datetime.combine(
        event.end_date or event.date, datetime.max.time()
    ).replace(tzinfo=zone)
    return start - CHECKIN_BEFORE, day_end.astimezone(timezone.utc)


def _status(event, member_id: int) -> str | None:
    from hubra.core.models.event import EventAttendance

    return db.session.scalar(
        select(EventAttendance.status).where(
            EventAttendance.event_id == event.id, EventAttendance.member_id == member_id
        )
    )


def _buttons(event, status: str | None, self_checkin: bool) -> list[dict]:
    if not self_checkin:
        return []
    now = datetime.now(timezone.utc)
    opens, closes = _window(event)
    start = opens + CHECKIN_BEFORE
    out = []
    if opens <= now <= closes and status != "present":
        out.append({"label": "Bin da", "style": 3, "id": f"attend:{event.id}:present"})
    if now < start and status != "excused":
        out.append(
            {"label": "Kann nicht", "style": 2, "id": f"attend:{event.id}:excused"}
        )
    if status in ("present", "excused") and now <= closes:
        out.append(
            {"label": "Zurücknehmen", "style": 2, "id": f"attend:{event.id}:clear"}
        )
    return [
        {"type": 2, "style": b["style"], "label": b["label"], "custom_id": b["id"]}
        for b in out
    ]


_SHOWN = {"present": "✅ da", "excused": "🙅 entschuldigt", "absent": "❌ abwesend"}


def trainings(conn, member) -> dict:
    from .commands import reply

    events = _expected_events(member)
    if not events:
        return reply(f"Keine Trainings in den nächsten {UPCOMING_DAYS} Tagen.")
    self_checkin = (conn.config or {}).get("self_checkin", True)
    lines, rows = [], []
    for e in events:
        status = _status(e, member.id)
        day = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")[e.date.weekday()]
        when = f"{day} {e.date:%d.%m.}" + (
            f" {e.time}" if e.time and not e.all_day else ""
        )
        lines.append(
            f"**{when}** · {e.title}" + (f" · {_SHOWN[status]}" if status else "")
        )
        buttons = _buttons(e, status, self_checkin)
        if buttons:
            for b in buttons:
                b["label"] = f"{b['label']} ({e.date:%d.%m.})"
            rows.append({"type": 1, "components": buttons})
    answer = reply("\n".join(lines))
    if rows:
        answer["data"]["components"] = rows[:5]
    return answer


def attend(conn, member, custom_id: str) -> dict:
    """``attend:<event>:present|excused|clear``."""
    from hubra.api.events import attendance
    from hubra.core.models.event import ClubEvent

    from .commands import _link_hint, reply

    if member is None:
        return reply(_link_hint())
    if not (conn.config or {}).get("self_checkin", True):
        return reply("Einchecken über Discord ist für diesen Verein ausgeschaltet.")
    parts = custom_id.split(":")
    if (
        len(parts) != 3
        or not parts[1].isdigit()
        or parts[2] not in ("present", "excused", "clear")
    ):
        return reply("Diese Schaltfläche gilt nicht mehr.")
    event = db.session.get(ClubEvent, int(parts[1]))
    if event is None:
        return reply("Dieses Training gibt es nicht mehr.")
    if event not in _expected_events(member):
        return reply("Du bist für dieses Training nicht eingeteilt.")
    wanted = None if parts[2] == "clear" else parts[2]
    now = datetime.now(timezone.utc)
    opens, closes = _window(event)
    if wanted == "present" and not (opens <= now <= closes):
        return reply("Einchecken geht ab einer Stunde vor Beginn bis Ende des Tages.")
    if wanted == "excused" and now >= opens + CHECKIN_BEFORE:
        return reply("Das Training hat schon begonnen; sag es bitte deinem Trainer.")
    if wanted is None and now > closes:
        return reply("Das geht nur bis Ende des Trainingstages.")
    attendance.set_status(event, member.id, wanted, actor_id=member.id)
    botlog.log(conn, "attendance", True, f"{member.name}: {parts[2]} · {event.title}")
    db.session.commit()
    text = {
        "present": f"✅ Eingecheckt: **{event.title}**.",
        "excused": f"Abgemeldet von **{event.title}**. Gute Besserung oder bis zum nächsten Mal!",
        None: f"Zurückgenommen für **{event.title}**.",
    }[wanted]
    return reply(text)


# ── Profile ──────────────────────────────────────────────────────────────────


def profile(member) -> dict:
    from hubra.core.models.finance import Invoice
    from hubra.core.models.function import FunctionHolder

    from .commands import reply
    from .messages import club_url

    today = date.today()
    functions = [
        h.function.name
        for h in db.session.scalars(
            select(FunctionHolder).where(FunctionHolder.member_id == member.id)
        ).all()
        if (h.term_start is None or h.term_start <= today)
        and (h.term_end is None or h.term_end >= today)
        and h.function is not None
    ]
    open_count = db.session.scalar(
        select(db.func.count(Invoice.id)).where(
            Invoice.member_id == member.id, Invoice.status.in_(("Open", "Overdue"))
        )
    )
    lines = [f"**{member.name}**"]
    if member.joined:
        lines.append(f"Mitglied seit {member.joined:%d.%m.%Y}")
    if member.tier is not None:
        lines.append(f"Mitgliedschaft: {member.tier.name}")
    groups = sorted(g.name for g in member.groups)
    if groups:
        lines.append(f"Gruppen: {', '.join(groups)}")
    if functions:
        lines.append(f"Funktionen: {', '.join(sorted(functions))}")
    lines.append(
        f"Offene Rechnungen: {open_count}" if open_count else "Keine offenen Rechnungen"
    )
    link = club_url("/app/account/settings")
    if link:
        lines.append(f"\nÄndern: {link}")
    return reply("\n".join(lines))
