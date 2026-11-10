"""Booking's Vor Ort list: who said they'll be in which room today.

Booking sends ``booking.onsite.today`` in the morning when someone is coming
(see docs/contracts.md in hubra-app/hubra); the club switches the post on
("Heute vor Ort posten") and may give it its own channel. It names members,
so it belongs in a channel only members see.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .messages import embed

_DAYS = (
    "Montag",
    "Dienstag",
    "Mittwoch",
    "Donnerstag",
    "Freitag",
    "Samstag",
    "Sonntag",
)
_MARK = {"going": "✅", "maybe": "❔", "here": "📍"}


def _when(person: dict) -> str:
    starts, ends = person.get("starts"), person.get("ends")
    if starts and ends:
        return f"{starts}–{ends}"
    if starts:
        return f"ab {starts}"
    if ends:
        return f"bis {ends}"
    return ""


def _line(person: dict) -> str:
    bits = [f"{_MARK.get(person.get('status'), '•')} {person.get('name') or '?'}"]
    when = _when(person)
    if when:
        bits.append(when)
    if person.get("key_holder"):
        bits.append("🔑")
    text = " · ".join(bits)
    if person.get("comment"):
        text += f" – {person['comment']}"
    return text


def today_embed(day: str | None, rooms: list[dict], link: str | None) -> dict[str, Any]:
    try:
        d = date.fromisoformat(day or "")
        label = f"{_DAYS[d.weekday()]}, {d:%d.%m.%Y}"
    except ValueError:
        label = day or ""
    fields = []
    for room in rooms:
        people = room.get("people") or []
        lines = [_line(p) for p in people]
        if not any(p.get("key_holder") for p in people):
            lines.append("_Noch niemand bringt den Schlüssel._")
        name = room.get("name") or "?"
        if room.get("location"):
            name += f" ({room['location']})"
        fields.append((f"🚪 {name}", "\n".join(lines)))
    return embed(
        f"📍 Heute vor Ort – {label}",
        "Wer heute kommt. Eintragen oder «Ich komme auch» in der App.",
        link,
        fields,
        inline=False,
        footer="✅ Komme · ❔ Vielleicht · 📍 Bin da · 🔑 bringt den Schlüssel",
    )
