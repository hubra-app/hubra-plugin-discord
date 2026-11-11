"""Direct messages from Hubra to a group of members.

Settings › Verbundene Apps › Discord › Mitglieder › "Direktnachricht": a
club admin writes a short message, picks who gets it (all members, a group,
a function, a tier, a Hubra role, a membership type) and the bot sends it
to each linked member of that set as a direct message, with the club's
name and colour. Needs the ``notify`` scope.

Discord only delivers to someone who shares the server with the bot and
accepts direct messages from its members; the others are counted, not
retried. The sending runs in the background (tasks.send_dm_broadcast) and
the result goes to the bot log.
"""

from __future__ import annotations

from sqlalchemy import select

from hubra.core import apps
from hubra.core.extensions import db

from . import api, botlog
from .api import DiscordError

MAX_RECIPIENTS = 300
MAX_TITLE = 200
MAX_TEXT = 1800


class DmError(Exception):
    """Something a club admin can fix, in their words."""


def recipients(conn, source: str) -> list[tuple[int, str]]:
    """[(member id, Discord user id)] of the linked members *source* fits."""
    from hubra.core.models.connected_app import ExternalAccount
    from hubra.core.models.member import Member

    from .roles import matches, sources

    if source not in {s["key"] for s in sources()}:
        raise DmError("Unbekannte Gruppe, Funktion oder Rolle.")
    linked = dict(
        db.session.execute(
            select(ExternalAccount.user_id, ExternalAccount.external_id).where(
                ExternalAccount.provider == "discord"
            )
        ).all()
    )
    if not linked:
        return []
    out = []
    for member in db.session.scalars(
        select(Member).where(
            Member.user_id.in_(list(linked)), Member.archived_at.is_(None)
        )
    ).all():
        # Former and blocked members get no messages from the club.
        if member.is_blocked:
            continue
        if matches(source, member):
            out.append((member.id, linked[member.user_id]))
    return out


def preview(conn, source: str) -> dict:
    found = recipients(conn, source)
    return {"count": len(found), "max": MAX_RECIPIENTS}


def body(title: str, text: str, who: str) -> dict:
    from .notify import _branded

    embed = {
        "title": title[:256],
        "description": text[:MAX_TEXT],
        "color": 0x6D3FD8,
        "footer": {"text": f"Von {who}"[:200]} if who else None,
    }
    embed = {k: v for k, v in embed.items() if v}
    return _branded({"embeds": [embed], "allowed_mentions": {"parse": []}})


def start(conn, source: str, title: str, text: str, who: str) -> dict:
    """Check and queue the message. Returns how many will get it."""
    from .hooks import after_commit
    from .tasks import send_dm_broadcast

    if not apps.has_scope(conn, "notify"):
        raise DmError(
            "Direktnachrichten sind für diese Verbindung nicht freigegeben "
            "(Bereich «Direktnachrichten» unter Darf)."
        )
    title = " ".join(str(title or "").split())[:MAX_TITLE]
    text = str(text or "").strip()
    if not title or not text:
        raise DmError("Gib einen Titel und einen Text ein.")
    if len(text) > MAX_TEXT:
        raise DmError(f"Der Text darf höchstens {MAX_TEXT} Zeichen haben.")
    found = recipients(conn, source)
    if not found:
        raise DmError("Niemand in dieser Auswahl hat Discord verknüpft.")
    if len(found) > MAX_RECIPIENTS:
        raise DmError(
            f"Das wären {len(found)} Personen; höchstens {MAX_RECIPIENTS} auf "
            "einmal. Wähle eine kleinere Gruppe."
        )
    after_commit(
        send_dm_broadcast.delay, conn.id, [u for _, u in found], title, text, who
    )
    botlog.log(conn, "dm", True, f'"{title}" an {len(found)} Personen ({who})')
    return {"queued": len(found)}


def send_all(conn, user_ids: list[str], title: str, text: str, who: str) -> dict:
    sent = closed = failed = 0
    message = body(title, text, who)
    for user_id in user_ids[:MAX_RECIPIENTS]:
        try:
            api.dm(user_id, message)
            sent += 1
        except DiscordError as exc:
            if exc.status == 403:
                closed += 1
            else:
                failed += 1
    detail = f'"{title}": {sent} gesendet'
    if closed:
        detail += f", {closed} nehmen keine Direktnachrichten an"
    if failed:
        detail += f", {failed} fehlgeschlagen"
    botlog.log(conn, "dm", not failed, detail)
    return {"sent": sent, "closed": closed, "failed": failed}
