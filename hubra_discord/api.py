"""Discord's REST API, as the Hubra bot and for the add-to-server sign-in.

The bot token is the platform's (manage › Platform settings › Discord); no
club ever holds one. Every call names the server it acts on, and callers only pass the
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

_log = logging.getLogger(__name__)

API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://hubra.club, 2)"

# Permission bits the bot asks for, by scope (never Administrator).
ADMINISTRATOR = 1 << 3
MANAGE_CHANNELS = 1 << 4
VIEW_CHANNEL = 1 << 10
SEND_MESSAGES = 1 << 11
EMBED_LINKS = 1 << 14
READ_MESSAGE_HISTORY = 1 << 16
MANAGE_ROLES = 1 << 28
# The bot's own nickname per server (the club's name).
CHANGE_NICKNAME = 1 << 26
# Scheduled events the bot made itself (create, edit, delete).
CREATE_EVENTS = 1 << 44
# Moderation (``moderate`` scope): remove people, delete reported messages.
KICK_MEMBERS = 1 << 1
MANAGE_GUILD = 1 << 5
MANAGE_MESSAGES = 1 << 13
CONNECT = 1 << 20

PERMISSION_NAMES = {
    MANAGE_CHANNELS: "Manage Channels",
    VIEW_CHANNEL: "View Channels",
    SEND_MESSAGES: "Send Messages",
    EMBED_LINKS: "Embed Links",
    READ_MESSAGE_HISTORY: "Read Message History",
    MANAGE_ROLES: "Manage Roles",
    CHANGE_NICKNAME: "Change Nickname",
    CREATE_EVENTS: "Create Events",
    KICK_MEMBERS: "Kick Members",
    MANAGE_MESSAGES: "Manage Messages",
}

CANNOT_DM_CODE = 50007

# Channel types announcements can go to: text and announcement channels.
TEXT_CHANNELS = (0, 5)


# Discord's JSON error codes worth naming (https://discord.com/developers/docs/topics/opcodes-and-status-codes#json).
ERROR_CODES = {
    10003: "Unknown Channel",
    10004: "Unknown Server",
    10008: "Unknown Message",
    10011: "Unknown Role",
    10013: "Unknown User",
    10070: "Unknown Scheduled Event",
    50001: "Missing Access",
    50007: "Cannot send messages to this user",
    50013: "Missing Permissions",
    50035: "Invalid Form Body",
}


class DiscordError(Exception):
    def __init__(
        self,
        message: str,
        status: int | None = None,
        retry_after=None,
        code: int | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
        # Discord's own error code (50013 Missing Permissions, ...).
        self.code = code
        # The call that failed, set by ``bot()``: "POST /channels/1/messages".
        self.call = ""

    @property
    def code_name(self) -> str:
        return ERROR_CODES.get(self.code or 0, "")

    @property
    def retryable(self) -> bool:
        """Rate limits, Discord's own errors and network trouble."""
        return self.status is None or self.status == 429 or self.status >= 500


def config(key: str) -> str:
    """One of the platform's Discord credentials: ``client_id``,
    ``client_secret``, ``bot_token`` or ``public_key``."""
    from hubra.core.services.platform_settings import discord

    return getattr(discord(), key) or ""


def missing_settings() -> list[str]:
    return [k for k in ("client_id", "client_secret", "bot_token") if not config(k)]


def _open(request: urllib.request.Request, timeout: float):
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310
            body = resp.read()
    except urllib.error.HTTPError as exc:
        retry_after = None
        detail = ""
        code = None
        try:
            data = json.loads(exc.read() or b"{}")
            detail = str(data.get("message", ""))
            if isinstance(data.get("code"), int):
                code = data["code"]
            if exc.code == 429:
                retry_after = float(data.get("retry_after"))
        except (ValueError, TypeError, AttributeError):
            pass
        text = f"Discord answered {exc.code}"
        if code:
            text += f" (code {code})"
        if detail:
            text += f": {detail}"
        raise DiscordError(text, exc.code, retry_after, code) from exc
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
        "Authorization": f"Bot {config('bot_token')}",
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
            exc.call = f"{method} {path}"
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
    me = member(guild_id, config("client_id")) or {}
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


def guild_permissions(guild_id: str) -> int:
    """The bot's permissions on the server (its roles and @everyone)."""
    me = member(guild_id, config("client_id")) or {}
    have = set(me.get("roles", [])) | {str(guild_id)}
    bits = 0
    for role in roles(guild_id):
        if role["id"] in have:
            bits |= int(role.get("permissions") or 0)
    return bits


def dm(user_id: str, body: dict) -> dict:
    """A direct message from the bot. Discord only allows it to someone who
    shares a server with the bot and allows DMs from that server."""
    channel = bot("POST", "/users/@me/channels", {"recipient_id": str(user_id)})
    return send(channel["id"], body)


def create_role(guild_id: str, name: str, reason: str) -> dict:
    return bot(
        "POST",
        f"/guilds/{guild_id}/roles",
        # No permissions of its own (Discord would copy @everyone's).
        {"name": name[:100], "permissions": "0", "mentionable": False, "hoist": False},
        reason=reason,
    )


def edit_role(guild_id: str, role_id: str, body: dict, reason: str) -> dict:
    return bot("PATCH", f"/guilds/{guild_id}/roles/{role_id}", body, reason=reason)


def delete_role(guild_id: str, role_id: str, reason: str) -> None:
    """Delete a role; one that is gone already is fine."""
    try:
        bot("DELETE", f"/guilds/{guild_id}/roles/{role_id}", reason=reason)
    except DiscordError as exc:
        if exc.status != 404:
            raise


def create_channel(guild_id: str, body: dict, reason: str) -> dict:
    return bot("POST", f"/guilds/{guild_id}/channels", body, reason=reason)


def channel(channel_id: str) -> dict | None:
    try:
        return bot("GET", f"/channels/{channel_id}")
    except DiscordError as exc:
        if exc.status == 404:
            return None
        raise


def edit_channel(channel_id: str, body: dict, reason: str) -> dict:
    return bot("PATCH", f"/channels/{channel_id}", body, reason=reason)


def create_event(guild_id: str, body: dict) -> dict:
    return bot("POST", f"/guilds/{guild_id}/scheduled-events", body)


def edit_event(guild_id: str, event_id: str, body: dict) -> dict:
    return bot("PATCH", f"/guilds/{guild_id}/scheduled-events/{event_id}", body)


def delete_event(guild_id: str, event_id: str) -> None:
    try:
        bot("DELETE", f"/guilds/{guild_id}/scheduled-events/{event_id}")
    except DiscordError as exc:
        if exc.status != 404:  # already gone
            raise


def kick(guild_id: str, user_id: str, reason: str) -> bool:
    """Remove someone from the server. False when they weren't on it."""
    try:
        bot("DELETE", f"/guilds/{guild_id}/members/{user_id}", reason=reason)
    except DiscordError as exc:
        if exc.status == 404:
            return False
        raise
    return True


def message(channel_id: str, message_id: str) -> dict | None:
    try:
        return bot("GET", f"/channels/{channel_id}/messages/{message_id}")
    except DiscordError as exc:
        if exc.status == 404:
            return None
        raise


def delete_message(channel_id: str, message_id: str, reason: str) -> bool:
    try:
        bot("DELETE", f"/channels/{channel_id}/messages/{message_id}", reason=reason)
    except DiscordError as exc:
        if exc.status == 404:
            return False
        raise
    return True


def edit_message(channel_id: str, message_id: str, body: dict) -> dict:
    return bot("PATCH", f"/channels/{channel_id}/messages/{message_id}", body)


def gateway_url() -> str:
    return (bot("GET", "/gateway/bot") or {}).get("url") or "wss://gateway.discord.gg"
