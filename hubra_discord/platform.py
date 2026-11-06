"""Hubra's own news on a club's server: new versions and maintenance.

Core's platform bot (``hubra.core.services.platform_bot``) hands every
connected club server the notice; this posts it to the main channel when
the club turned it on (Settings › Verbundene Apps › Discord: "Maintenance
notices", off by default). New versions come as the ``hubra.released``
event now (changelog.py); ``release`` here is for older Hubra versions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from hubra.core import apps

HUBRA_VIOLET = 0x6D4AFF
ORANGE = 0xF39C12
GREEN = 0x2ECC71

TOGGLES = {
    "release": "hubra_news",
    "maintenance_planned": "maintenance_notices",
    "maintenance_started": "maintenance_notices",
    "maintenance_ended": "maintenance_notices",
    "maintenance_cancelled": "maintenance_notices",
}


def _english() -> bool:
    from hubra.core.tenant.context import current_tenant

    tenant = current_tenant()
    return str(getattr(tenant, "locale", "") or "").lower().startswith("en")


def _when(iso: str | None) -> str:
    if not iso:
        return ""
    from hubra.core.tenant.context import current_tenant

    try:
        at = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return ""
    try:
        zone = ZoneInfo(getattr(current_tenant(), "timezone", None) or "Europe/Zurich")
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("Europe/Zurich")
    return f"{at.astimezone(zone):%d.%m.%Y %H:%M}"


def notice_embed(kind: str, data: dict) -> dict[str, Any] | None:
    """The embed for one notice, in the club's language (German or English)."""
    en = _english()
    message = str(data.get("message") or "")
    if kind == "release":
        version = str(data.get("version") or "")
        if not version:
            return None
        fields = []
        for heading, items in (data.get("sections") or {}).items():
            items = list(items or [])
            text = "\n".join(f"• {i}" for i in items[:5])
            if len(items) > 5:
                more = len(items) - 5
                text += (
                    f"\n… {'and' if en else 'und'} {more} {'more' if en else 'weitere'}"
                )
            if text:
                fields.append({"name": str(heading)[:250], "value": text[:1000]})
        return {
            "title": f"Hubra {version}",
            "description": "What's new in Hubra" if en else "Neu in Hubra",
            "color": HUBRA_VIOLET,
            "fields": fields[:6],
        }
    if kind == "maintenance_planned":
        start = _when(data.get("start"))
        minutes = data.get("minutes")
        length = (
            f" ({'about' if en else 'etwa'} {minutes} {'minutes' if en else 'Minuten'})"
            if minutes
            else ""
        )
        text = (
            f"Hubra will be briefly unavailable from {start}{length}."
            if en
            else f"Hubra ist ab {start}{length} kurz nicht erreichbar."
        )
        return {
            "title": "Planned maintenance" if en else "Geplante Wartung",
            "description": f"{text}\n{message}".strip(),
            "color": ORANGE,
        }
    if kind == "maintenance_started":
        until = _when(data.get("until"))
        text = (
            (
                "Hubra is being updated"
                + (f" until about {until}" if until else "")
                + "."
            )
            if en
            else (
                "Hubra wird aktualisiert"
                + (f", voraussichtlich bis {until}" if until else "")
                + "."
            )
        )
        return {
            "title": "Maintenance" if en else "Wartung",
            "description": f"{text}\n{message}".strip(),
            "color": ORANGE,
        }
    if kind == "maintenance_ended":
        return {
            "title": "Hubra is back" if en else "Hubra ist wieder da",
            "description": "The maintenance is over."
            if en
            else "Die Wartung ist vorbei.",
            "color": GREEN,
        }
    if kind == "maintenance_cancelled":
        return {
            "title": "Maintenance cancelled" if en else "Wartung abgesagt",
            "description": "The planned maintenance does not take place."
            if en
            else "Die geplante Wartung findet nicht statt.",
            "color": GREEN,
        }
    return None


def platform_notice(conn, kind: str, data: dict) -> bool:
    """Queue *kind* for this server's main channel if the club wants it.
    Called inside the club. True when queued.

    ``release`` comes only from a Hubra without the ``hubra.released`` event
    (changelog.py); it goes to the changelog channel like that event, once
    per version."""
    from hubra.core.extensions import db

    from . import changelog
    from .app import channel_for
    from .messages import payload
    from .tasks import send_announcement

    toggle = TOGGLES.get(kind)
    config = conn.config or {}
    where = "changelog" if kind == "release" else "hubra"
    channel = (
        channel_for(config, "changelog")
        if kind == "release"
        else config.get("channel_id")
    )
    if not toggle or not config.get(toggle) or not channel:
        return False
    if not apps.has_scope(conn, "announce"):
        return False
    embed = notice_embed(kind, data or {})
    if embed is None:
        return False
    if kind == "release":
        done = changelog.mark("hubra", str((data or {}).get("version") or ""))
        if changelog.seen(conn, done):
            return False
        changelog.remember(conn, done)
        db.session.commit()
    send_announcement.delay(conn.id, payload(None, [embed]), where)
    return True
