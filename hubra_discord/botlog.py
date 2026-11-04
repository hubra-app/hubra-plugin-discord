"""The bot's own event log.

Everything the bot does or leaves out for a club goes to three places:

- the connection's activity list in Hubra (Settings › Verbundene Apps ›
  Discord › Details), through ``hubra.core.apps.record``;
- the server log (Python logging, ``hubra_discord``), with the club and
  Discord server, so platform admins see it in the container logs;
- optionally a log channel on the club's own Discord server
  (``log_channel_id``), for errors only or for everything (``log_level``).

A failure to post in the log channel is recorded in Hubra but never posted
again, so a broken log channel can't loop.
"""

from __future__ import annotations

import logging

from hubra.core import apps

_log = logging.getLogger("hubra_discord")

LEVELS = ("errors", "all")
RED = 0xE74C3C
GREEN = 0x2ECC71
GREY = 0x95A5A6

TITLES = {
    "announced": "📣 Posted",
    "skipped": "⏭️ Not posted",
    "roles": "🎭 Roles",
    "events": "📅 Discord events",
    "profile": "🪪 Bot profile",
    "joined": "👋 Joined",
    "dm": "✉️ Direct message",
    "registration": "📝 Sign-up",
    "voted": "🗳️ Vote",
    "attendance": "✅ Attendance",
    "command": "⌨️ Command",
    "test": "🧪 Test",
    "updated": "✏️ Updated",
    "channels": "#️⃣ Channels",
    "audit": "🔒 Permission check",
    "layout": "🧱 Channel layout",
    "stats": "📊 Stats channel",
    "weekly": "🗓️ Monday posts",
    "leavers": "🚪 Left the club",
    "report": "🚩 Report",
    "guests": "👋 New people",
    "feedback": "💬 Feedback",
}


def log(conn, action: str, ok: bool = True, detail: str = "") -> None:
    """Note what the bot did (or why it didn't) for this club."""
    detail = (detail or "")[:500]
    apps.record(conn, action, ok, detail)
    level = logging.INFO if ok else logging.WARNING
    _log.log(
        level,
        "discord club=%s guild=%s %s %s: %s",
        conn.tenant_id,
        conn.external_id,
        action,
        "ok" if ok else "FAILED",
        detail,
    )
    config = conn.config or {}
    channel = config.get("log_channel_id")
    if not channel:
        return
    if ok and config.get("log_level", "errors") != "all":
        return
    from .hooks import after_commit
    from .tasks import post_log

    after_commit(post_log.delay, conn.id, action, ok, detail)


def skipped(conn, what: str, why: str) -> None:
    """Something the bot left out on purpose (a setting, no channel, ...)."""
    log(conn, "skipped", True, f"{what}: {why}"[:500])


def embed(action: str, ok: bool, detail: str) -> dict:
    title = TITLES.get(action, action)
    if not ok:
        title = f"❌ {title}"
    color = RED if not ok else GREY if action == "skipped" else GREEN
    return {"title": title[:256], "description": detail[:2000] or "–", "color": color}
