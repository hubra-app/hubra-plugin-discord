"""Slash commands and buttons: what members do in Discord.

Discord sends every click and command to ``/api/v1/discord/interactions``
(see routes.py), signed with the application's key. The server it comes
from must be connected to a club, and the person must have linked their
Discord account to a member of that club; Hubra then applies the same
rules as in the app. Every answer is only visible to the person.
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import select

from hubra.core import apps
from hubra.core.errors import ApiError
from hubra.core.extensions import db

from . import api

_log = logging.getLogger(__name__)

EPHEMERAL = 1 << 6
MANAGE_GUILD = 1 << 5

COMMANDS = [
    {
        "name": "events",
        "description": "Die nächsten Anlässe des Vereins",
        "description_localizations": {
            "en-GB": "The club's next events",
            "en-US": "The club's next events",
        },
        "type": 1,
        "contexts": [0],
    },
    {
        "name": "verbinden",
        "description": "Dein Discord-Konto mit Hubra verknüpfen",
        "description_localizations": {
            "en-GB": "Link your Discord account to Hubra",
            "en-US": "Link your Discord account to Hubra",
        },
        "type": 1,
        "contexts": [0],
    },
    {
        "name": "hubra",
        "description": "Status der Hubra-Verbindung (Admins)",
        "description_localizations": {
            "en-GB": "Hubra connection status (admins)",
            "en-US": "Hubra connection status (admins)",
        },
        "type": 1,
        "contexts": [0],
        # Shown to server managers only; Hubra checks its own right too.
        "default_member_permissions": str(MANAGE_GUILD),
    },
]


def register() -> None:
    """Install the commands for the Hubra application (all servers)."""
    app_id = api.config("DISCORD_CLIENT_ID")
    api.bot("PUT", f"/applications/{app_id}/commands", COMMANDS)


def register_after_commit(conn) -> None:
    from .hooks import after_commit
    from .tasks import register_commands

    after_commit(register_commands.delay)


# ── Answers ──────────────────────────────────────────────────────────────────


def reply(text: str) -> dict:
    return {
        "type": 4,
        "data": {
            "content": text[:2000],
            "flags": EPHEMERAL,
            "allowed_mentions": {"parse": []},
        },
    }


def _link_hint() -> str:
    from hubra.core.services.email import _get_sso_base_url

    base = _get_sso_base_url()
    where = f"{base}/account/verknuepfungen" if base else "Mein Konto › Verknüpfungen"
    return f"Verknüpfe dein Discord-Konto mit Hubra: {where}"


def club_member(discord_user_id: str):
    """The club's member behind a Discord user, or None (not linked, not a
    member, archived or blocked). Call inside the club."""
    from hubra.core.models.connected_app import ExternalAccount
    from hubra.core.models.member import Member

    user_id = db.session.scalar(
        select(ExternalAccount.user_id).where(
            ExternalAccount.provider == "discord",
            ExternalAccount.external_id == str(discord_user_id),
        )
    )
    if user_id is None:
        return None
    member = db.session.scalar(select(Member).where(Member.user_id == user_id))
    if member is None or member.archived_at is not None or member.is_blocked:
        return None
    return member


def _may(member, key: str) -> bool:
    from hubra.api.permissions import member_has_permission, member_is_admin

    return member_is_admin(member) or member_has_permission(member, key)


def handle(conn, interaction: dict) -> dict:
    """Answer one command or button click from the club's server."""
    user = (interaction.get("member") or {}).get("user") or {}
    member = club_member(user.get("id", "")) if user.get("id") else None
    kind = interaction.get("type")
    data = interaction.get("data") or {}

    if kind == 2:
        name = data.get("name")
        if name == "events":
            return _events(conn, member)
        if name == "verbinden":
            if member is not None:
                return reply("✅ Dein Discord-Konto ist bereits mit Hubra verknüpft.")
            return reply(_link_hint())
        if name == "hubra":
            return _status(conn, member)
        return reply("Unbekannter Befehl.")

    if kind == 3:
        if not apps.has_scope(conn, "commands"):
            return reply("Anmelden über Discord ist für diesen Verein ausgeschaltet.")
        action, _, raw = str(data.get("custom_id", "")).partition(":")
        if action in ("register", "unregister") and raw.isdigit():
            return _registration(conn, member, int(raw), action == "register")
        return reply("Diese Schaltfläche gilt nicht mehr.")

    return reply("Das kann Hubra nicht.")


def _events(conn, member) -> dict:
    from hubra.core.models.event import ClubEvent

    from .messages import club_url

    query = select(ClubEvent).where(ClubEvent.date >= date.today())
    if member is None:  # people who aren't (linked) members see public ones
        query = query.where(ClubEvent.is_public.is_(True))
    events = db.session.scalars(query.order_by(ClubEvent.date).limit(5)).all()
    if not events:
        return reply("Keine kommenden Anlässe.")
    lines = [
        f"**{e.date:%d.%m.%Y}**{' ' + e.time if e.time and not e.all_day else ''} · {e.title}"
        for e in events
    ]
    link = club_url("/app/operations/events")
    if link:
        lines.append(f"\n{link}")
    if member is None:
        lines.append(f"\n{_link_hint()}")
    return reply("\n".join(lines))


def _status(conn, member) -> dict:
    if member is None or not _may(member, "apps.apps.manage"):
        return reply("Nur wer im Verein verbundene Apps verwaltet, sieht den Status.")
    from hubra.core.models.connected_app import ExternalAccount
    from hubra.core.models.member import Member

    linked = db.session.scalar(
        select(db.func.count(Member.id))
        .join(ExternalAccount, ExternalAccount.user_id == Member.user_id)
        .where(ExternalAccount.provider == "discord", Member.archived_at.is_(None))
    )
    config = conn.config or {}
    lines = [
        f"Verbunden mit **{conn.label}** seit {conn.confirmed_at:%d.%m.%Y}"
        if conn.confirmed_at
        else f"Verbunden mit **{conn.label}**",
        f"Ankündigungen: {'<#' + config['channel_id'] + '>' if config.get('channel_id') else 'kein Kanal gewählt'}",
        f"Rollen-Zuordnungen: {len(config.get('role_map') or [])}",
        f"Mitglieder mit verknüpftem Discord: {linked or 0}",
    ]
    if conn.last_error:
        lines.append(f"Letzter Fehler: {conn.last_error}")
    return reply("\n".join(lines))


def _registration(conn, member, event_id: int, register: bool) -> dict:
    from hubra.api.events.services import EventService
    from hubra.core.models.event import ClubEvent

    if member is None:
        return reply(_link_hint())
    if not _may(member, "events.events.register"):
        return reply("Du darfst dich nicht selbst für Anlässe anmelden.")
    event = db.session.get(ClubEvent, event_id)
    if event is None:
        return reply("Diesen Anlass gibt es nicht mehr.")
    try:
        if register:
            reg = EventService.register(
                event_id=event_id, member_id=member.id, own=True
            )
            text = {
                "pending": f"Angemeldet für **{event.title}**; ein Organisator bestätigt deine Anmeldung.",
                "waitlisted": f"**{event.title}** ist voll; du bist auf der Warteliste.",
            }.get(reg.status, f"✅ Angemeldet für **{event.title}**.")
        else:
            EventService.cancel_registration(event_id, member.id, own=True)
            text = f"Abgemeldet von **{event.title}**."
        db.session.commit()
    except ApiError as exc:
        db.session.rollback()
        text = {
            "REGISTRATION_CLOSED": "Die Anmeldung ist geschlossen.",
            "CONFLICT": f"Du bist bereits für **{event.title}** angemeldet.",
            "NOT_FOUND": "Du bist nicht angemeldet.",
        }.get(exc.code, exc.message)
    apps.record(
        conn,
        "registration",
        True,
        f"{member.name}: {'an' if register else 'ab'} · {event.title}",
    )
    db.session.commit()
    return reply(text)


def event_buttons(event) -> list[dict]:
    return [
        {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 1,
                    "label": "Anmelden",
                    "custom_id": f"register:{event.id}",
                },
                {
                    "type": 2,
                    "style": 2,
                    "label": "Abmelden",
                    "custom_id": f"unregister:{event.id}",
                },
            ],
        }
    ]
