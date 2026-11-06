"""Changelogs on the club's server: new Hubra versions and plugin updates.

Hubra sends ``hubra.released`` once per new version in every club, and
``plugin.released`` once per new plugin version in the clubs that have that
plugin on, each with the version's changelog section (``services.changelogs``
in Hubra). This posts them when the club opted in (Settings › Verbundene
Apps › Discord › Ankündigungen: "Changelog: neue Hubra-Versionen",
"Changelog: Plugin-Updates", both off by default), in the channel picked
for ``changelog`` (an announcements channel or a changelog channel of its
own), else in the main channel.

Nothing is posted twice:

* the event comes through Hubra's outbox with a delivery id (``event_id``);
  the handler notes the delivery id and the version as posted
  (``config["changelog_sent"]``) in the same transaction that marks the
  delivery done, so a delivery that is retried, or the same version sent
  again, is skipped;
* the post carries a Discord nonce made from the delivery id and the
  server, so a send job that runs twice (a worker that died after posting)
  is dropped by Discord.
"""

from __future__ import annotations

import hashlib
from typing import Any

KEY = "changelog_sent"
MAX_KEPT = 200
HUBRA_VIOLET = 0x6D4AFF
# Discord's limits: an embed's description, a nonce.
MAX_TEXT = 3800
NONCE_LENGTH = 25

TOGGLES = {"hubra": "hubra_news", "plugin": "plugin_news"}
SWITCH_NAMES = {
    "hubra_news": "Changelog: neue Hubra-Versionen",
    "plugin_news": "Changelog: Plugin-Updates",
}


def mark(source: str, version: str) -> str:
    return f"{source}@{version}"


def nonce(delivery_id: str | None, conn_id: int, mark_: str) -> str:
    """The same delivery on the same server always gets the same nonce."""
    raw = f"{delivery_id or mark_}:{conn_id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:NONCE_LENGTH]


def sent(conn) -> list[str]:
    return list((conn.config or {}).get(KEY) or [])


def seen(conn, *keys: str | None) -> bool:
    known = set(sent(conn))
    return any(k and k in known for k in keys)


def remember(conn, *keys: str | None) -> None:
    """Note *keys* as posted. The caller commits (with the delivery)."""
    config = dict(conn.config or {})
    known = [k for k in sent(conn) if k not in keys]
    known.extend(k for k in keys if k)
    config[KEY] = known[-MAX_KEPT:]
    conn.config = config


# ── Messages ────────────────────────────────────────────────────────────────


def _english() -> bool:
    from hubra.core.tenant.context import current_tenant

    try:
        tenant = current_tenant()
    except RuntimeError:
        return False
    return str(getattr(tenant, "locale", "") or "").lower().startswith("en")


def _cut(text: str, en: bool) -> str:
    text = text.strip()
    if len(text) <= MAX_TEXT:
        return text
    more = (
        "… more in Hubra under What's new"
        if en
        else "… mehr in Hubra unter Neuigkeiten"
    )
    return text[: MAX_TEXT - len(more) - 1].rstrip() + "\n" + more


def hubra_embed(data: dict) -> dict[str, Any] | None:
    """``hubra.released`` as one embed: the version's whole changelog section,
    in German when it is translated and the club speaks German."""
    version = str(data.get("version") or "")
    if not version:
        return None
    en = _english()
    sections = (None if en else data.get("sections_de")) or data.get("sections") or {}
    parts = []
    for heading, items in sections.items():
        lines = "\n".join(f"• {i}" for i in items or [])
        if lines:
            parts.append(f"**{heading}**\n{lines}")
    intro = "What's new in Hubra" if en else "Neu in Hubra"
    if data.get("date"):
        intro += f" · {data['date']}"
    return {
        "title": f"Hubra {version}"[:256],
        "description": _cut(f"{intro}\n\n" + "\n\n".join(parts), en),
        "color": HUBRA_VIOLET,
    }


def plugin_embed(data: dict) -> dict[str, Any] | None:
    """``plugin.released`` as one embed: the version's changelog section."""
    version = str(data.get("version") or "")
    if not version:
        return None
    en = _english()
    name = str(data.get("name") or data.get("plugin_id") or "Plugin")
    previous = data.get("previous_version")
    intro = (
        f"{'Plugin update' if en else 'Plugin-Update'}: {previous} → {version}"
        if previous and previous != version
        else ("New plugin version" if en else "Neue Plugin-Version")
    )
    notes = str(data.get("notes") or "").strip()
    return {
        "title": f"{name} {version}"[:256],
        "description": _cut(f"{intro}\n\n{notes}" if notes else intro, en),
        "color": HUBRA_VIOLET,
    }


# ── Posting ─────────────────────────────────────────────────────────────────


def queue(tenant_id, source: str, data: dict, delivery_id: str | None) -> int:
    """Queue the post on every server of the club that wants it. Returns how
    many. Called by the event handler (hooks.py), inside the club."""
    from . import botlog
    from .app import channel_for
    from .hooks import _connections, after_commit
    from .messages import payload
    from .tasks import send_announcement

    toggle = TOGGLES[source]
    if source == "hubra":
        what_mark = mark("hubra", str(data.get("version") or ""))
        build = hubra_embed
    else:
        what_mark = mark(
            str(data.get("plugin_id") or ""), str(data.get("version") or "")
        )
        build = plugin_embed
    what = f"Changelog {what_mark}"
    queued = 0
    for conn in _connections(tenant_id, "announce"):
        config = conn.config or {}
        if not config.get(toggle):
            botlog.skipped(conn, what, f'"{SWITCH_NAMES[toggle]}" is off')
            continue
        if not channel_for(config, "changelog"):
            botlog.skipped(
                conn, what, "no channel picked for the changelog or the main channel"
            )
            continue
        if seen(conn, delivery_id, what_mark):
            continue  # posted already: a retried delivery, or sent again
        embed = build(data)
        if embed is None:
            continue
        body = payload(None, [embed])
        body["nonce"] = nonce(delivery_id, conn.id, what_mark)
        body["enforce_nonce"] = True
        remember(conn, delivery_id, what_mark)
        after_commit(send_announcement.delay, conn.id, body, "changelog")
        queued += 1
    return queued
