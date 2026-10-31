"""Discord's REST API, as the Hubra bot and for the add-to-server sign-in.

The bot token is the platform's (``DISCORD_BOT_TOKEN``); no club ever holds
one. Every call names the server it acts on, and callers only pass the
server of a connection the club confirmed.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from flask import current_app

_log = logging.getLogger(__name__)

API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://hubra.club, 2)"

# Permission bits the bot asks for, by scope (never Administrator).
VIEW_CHANNEL = 1 << 10
SEND_MESSAGES = 1 << 11
EMBED_LINKS = 1 << 14
MANAGE_ROLES = 1 << 28

# Channel types announcements can go to: text and announcement channels.
TEXT_CHANNELS = (0, 5)


class DiscordError(Exception):
    def __init__(self, message: str, status: int | None = None, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        """Rate limits, Discord's own errors and network trouble."""
        return self.status is None or self.status == 429 or self.status >= 500


def config(key: str) -> str:
    return current_app.config.get(key, "") or ""


def missing_settings() -> list[str]:
    return [
        k
        for k in ("DISCORD_CLIENT_ID", "DISCORD_CLIENT_SECRET", "DISCORD_BOT_TOKEN")
        if not config(k)
    ]


def _open(request: urllib.request.Request, timeout: float):
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310
            body = resp.read()
    except urllib.error.HTTPError as exc:
        retry_after = None
        detail = ""
        try:
            data = json.loads(exc.read() or b"{}")
            detail = str(data.get("message", ""))
            if exc.code == 429:
                retry_after = float(data.get("retry_after"))
        except (ValueError, TypeError):
            pass
        raise DiscordError(
            f"Discord answered {exc.code}{': ' + detail if detail else ''}",
            exc.code,
            retry_after,
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise DiscordError(f"Discord unreachable: {exc}") from exc
    return json.loads(body) if body else None


def bot(
    method: str,
    path: str,
    body: dict | None = None,
    *,
    reason: str | None = None,
    timeout: float = 10,
    wait: float = 10,
) -> Any:
    """Call the API as the bot. Short rate limits are waited out (up to
    *wait* seconds in all); longer ones raise ``DiscordError``."""
    headers = {
        "Authorization": f"Bot {config('DISCORD_BOT_TOKEN')}",
        "User-Agent": USER_AGENT,
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    if reason:
        # Shows in the server's audit log, so admins see why a role changed.
        headers["X-Audit-Log-Reason"] = urllib.parse.quote(reason[:500])
    data = json.dumps(body).encode("utf-8") if body is not None else None
    waited = 0.0
    while True:
        request = urllib.request.Request(
            f"{API}{path}", data=data, headers=headers, method=method
        )
        try:
            return _open(request, timeout)
        except DiscordError as exc:
            pause = exc.retry_after
            if exc.status != 429 or pause is None or waited + pause > wait:
                raise
            time.sleep(pause)
            waited += pause


def form(path: str, fields: dict) -> Any:
    """POST a form as the application (OAuth token exchange and revoke)."""
    request = urllib.request.Request(
        f"{API}{path}",
        data=urllib.parse.urlencode(fields).encode(),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    return _open(request, 10)


# ── What the bot does ────────────────────────────────────────────────────────


def channels(guild_id: str) -> list[dict]:
    return bot("GET", f"/guilds/{guild_id}/channels") or []


def roles(guild_id: str) -> list[dict]:
    return bot("GET", f"/guilds/{guild_id}/roles") or []


def member(guild_id: str, user_id: str) -> dict | None:
    """A user's membership on the server, or None when they aren't on it."""
    try:
        return bot("GET", f"/guilds/{guild_id}/members/{user_id}")
    except DiscordError as exc:
        if exc.status == 404:
            return None
        raise


def assignable_roles(guild_id: str) -> list[dict]:
    """The server's roles the bot can give: below its own highest role, not
    @everyone and not managed by an integration."""
    all_roles = roles(guild_id)
    me = member(guild_id, config("DISCORD_CLIENT_ID")) or {}
    by_id = {r["id"]: r for r in all_roles}
    top = max(
        (by_id[r]["position"] for r in me.get("roles", []) if r in by_id), default=0
    )
    return [
        r
        for r in all_roles
        if r["id"] != str(guild_id) and not r.get("managed") and r["position"] < top
    ]


def add_role(guild_id: str, user_id: str, role_id: str, reason: str) -> None:
    bot("PUT", f"/guilds/{guild_id}/members/{user_id}/roles/{role_id}", reason=reason)


def remove_role(guild_id: str, user_id: str, role_id: str, reason: str) -> None:
    bot(
        "DELETE", f"/guilds/{guild_id}/members/{user_id}/roles/{role_id}", reason=reason
    )


def send(channel_id: str, body: dict) -> dict:
    return bot("POST", f"/channels/{channel_id}/messages", body)


def leave(guild_id: str) -> None:
    bot("DELETE", f"/users/@me/guilds/{guild_id}")
