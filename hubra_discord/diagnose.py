"""Why the bot couldn't do something, in words a club admin can act on.

Discord only answers "403 Missing Permissions" or "Missing Access". This
works out what the bot actually lacks: its roles on the server, then the
channel's permission overrides (the same order Discord uses), and names the
permissions and the channel.
"""

from __future__ import annotations

import logging

from . import api
from .api import DiscordError

_log = logging.getLogger(__name__)

ALL = (1 << 53) - 1

# What each kind of work needs in the channel it touches.
SEND = api.VIEW_CHANNEL | api.SEND_MESSAGES | api.EMBED_LINKS
NEEDS = {
    "send": SEND,
    "channel": api.VIEW_CHANNEL | api.MANAGE_CHANNELS | api.MANAGE_ROLES,
}


def _names(bits: int) -> list[str]:
    return [name for bit, name in api.PERMISSION_NAMES.items() if bits & bit]


def channel_permissions(guild_id: str, channel: dict) -> int:
    """The bot's permissions in *channel* (a channel object from Discord)."""
    me = api.member(guild_id, api.config("client_id")) or {}
    my_roles = set(me.get("roles", []))
    by_id = {r["id"]: int(r.get("permissions") or 0) for r in api.roles(guild_id)}
    bits = by_id.get(str(guild_id), 0)
    for role in my_roles:
        bits |= by_id.get(role, 0)
    if bits & api.ADMINISTRATOR:
        return ALL
    overwrites = {o["id"]: o for o in channel.get("permission_overwrites") or []}
    everyone = overwrites.get(str(guild_id))
    if everyone:
        bits &= ~int(everyone.get("deny") or 0)
        bits |= int(everyone.get("allow") or 0)
    allow = deny = 0
    for role in my_roles:
        o = overwrites.get(role)
        if o and o.get("type") in (0, "0", "role"):
            allow |= int(o.get("allow") or 0)
            deny |= int(o.get("deny") or 0)
    bits = (bits & ~deny) | allow
    mine = overwrites.get(str(api.config("client_id")))
    if mine:
        bits &= ~int(mine.get("deny") or 0)
        bits |= int(mine.get("allow") or 0)
    return bits


def channel_label(channel_id: str | None) -> str:
    if not channel_id:
        return "the channel"
    try:
        found = api.channel(channel_id)
    except DiscordError:
        found = None
    return (
        f"#{found['name']}" if found and found.get("name") else f"channel {channel_id}"
    )


def missing_in_channel(
    guild_id: str, channel_id: str, need: int = SEND
) -> tuple[str, list[str]] | None:
    """(``#name``, missing permission names), or None when the channel is
    gone. Best effort: a lookup that fails returns no names."""
    try:
        found = api.channel(channel_id)
    except DiscordError as exc:
        if exc.status in (403,):
            # The bot can't even see the channel.
            return f"channel {channel_id}", _names(api.VIEW_CHANNEL)
        return f"channel {channel_id}", []
    if found is None:
        return None
    label = f"#{found.get('name') or channel_id}"
    try:
        have = channel_permissions(guild_id, found)
    except DiscordError:
        return label, []
    return label, _names(need & ~have)


def describe(
    exc: DiscordError,
    *,
    doing: str = "",
    guild_id: str | None = None,
    channel_id: str | None = None,
    need: int = SEND,
) -> str:
    """One line: what failed, where, Discord's code, and the fix."""
    head = f"{doing} failed" if doing else "Discord refused"
    where = ""
    reason = ""
    if channel_id and guild_id and exc.status in (403, 404):
        try:
            found = missing_in_channel(guild_id, channel_id, need)
        except DiscordError:
            found = ("", [])
        if found is None:
            reason = (
                f"channel {channel_id} no longer exists; pick another one in "
                "the Discord settings"
            )
        else:
            label, missing = found
            where = f" in {label}" if label else ""
            if missing:
                reason = (
                    f"the bot lacks {', '.join(missing)}. Give the Hubra "
                    "role these permissions in the channel's settings "
                    "(Edit channel › Permissions) or on the server"
                )
            elif exc.status == 403:
                reason = (
                    "Discord refused although the bot's permissions look right; "
                    "check the channel's permission overrides for the Hubra role"
                )
    elif exc.status == 403 and exc.code == api.CANNOT_DM_CODE:
        reason = "the person doesn't accept direct messages from this server"
    elif exc.status == 403 and "scheduled-events" in exc.call:
        reason = (
            "the bot lacks Create Events (or Manage Events) on the server; "
            "give the Hubra role that permission"
        )
    elif exc.status == 403 and "/roles" in exc.call:
        reason = (
            "the bot can only give roles below its own; move the Hubra role "
            "above the roles it should manage (Server settings › Roles) and "
            "give it Manage Roles"
        )
    elif exc.status == 403:
        reason = "the bot lacks a permission for this on the server"
    elif exc.status == 404:
        reason = "the server, channel, role or message is gone, or the bot was removed"
    elif exc.status == 401:
        reason = "the bot token is wrong or was reset (manage › Discord)"
    code = ""
    if exc.code:
        code = f"Discord {exc.code}" + (f" {exc.code_name}" if exc.code_name else "")
    elif exc.status:
        code = f"HTTP {exc.status}"
    if exc.call:
        code = f"{code}, {exc.call}" if code else exc.call
    text = f"{head}{where}: {reason or exc}"
    if reason and code:
        text += f" ({code})"
    return text
