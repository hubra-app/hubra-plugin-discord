"""The bot's look on one club's server, and the club's look on its posts.

Discord lets a bot have its own nickname, avatar and bio per server
(``PATCH /guilds/{id}/members/@me``), so on a club's server the bot can be
"FC Example" with the club's logo while every other server still sees
Hubra. The application's own name, icon and description stay the
platform's (Developer Portal).

* ``club_profile`` (default on): nickname = the club's name, avatar = the
  club's logo (PNG, JPEG, GIF or WebP up to 2 MB; an SVG logo is left out),
  bio = a line naming the club. Off resets all three to the app's own.
  The nickname needs Change Nickname on the server; servers added before
  2.1 lack it until an admin adds it to the Hubra role, so it is set on its
  own and a refusal doesn't stop the avatar and bio.
* ``club_embeds`` (default on): announcements use the club's main colour
  and show the club's name and logo at the top.

What was applied is kept as a hash in the connection's config
(``profile_applied``), so the nightly run only calls Discord when the
club's name or logo changed.
"""

from __future__ import annotations

import base64
import hashlib
import logging

from flask import current_app

from . import api
from .api import DiscordError

_log = logging.getLogger(__name__)

AVATAR_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")
MAX_AVATAR_BYTES = 2 * 1024 * 1024
NICK_MAX = 32
BIO_MAX = 190


def club_name(tenant) -> str:
    from hubra.core.tenant.club_settings import club_setting

    return (club_setting(tenant, "club_name") or tenant.name or "").strip()


def avatar_data_url(tenant) -> str | None:
    """The club logo as a data URL Discord takes, or None."""
    from hubra.core.tenant.club_settings import club_logo

    logo = club_logo(tenant)
    if logo is None:
        return None
    mime, data = logo
    if mime not in AVATAR_TYPES or len(data) > MAX_AVATAR_BYTES:
        return None
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def club_color(tenant) -> int | None:
    from hubra.core.tenant.club_settings import club_setting

    value = str(club_setting(tenant, "primary_color") or "").lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    try:
        return int(value, 16) if len(value) == 6 else None
    except ValueError:
        return None


def logo_url(tenant) -> str | None:
    """A public link to the club logo (raster only: Discord shows no SVG)."""
    from hubra.core.tenant.club_settings import club_logo, club_logo_version

    logo = club_logo(tenant)
    if logo is None or logo[0] not in AVATAR_TYPES:
        return None
    base = (current_app.config.get("API_PUBLIC_URL") or "").rstrip("/")
    if not base:
        return None
    return (
        f"{base}/api/v1/system/clubs/{tenant.slug}/logo?v={club_logo_version(tenant)}"
    )


def desired(conn, tenant) -> dict:
    """What the bot's member profile on this server should be."""
    if not (conn.config or {}).get("club_profile", True):
        return {"nick": None, "avatar": None, "bio": None}
    name = club_name(tenant)
    return {
        "nick": name[:NICK_MAX] or None,
        "avatar": avatar_data_url(tenant),
        "bio": _bio(tenant, name)[:BIO_MAX] or None,
    }


def _bio(tenant, name: str) -> str:
    from hubra.core.tenant.club_settings import club_setting

    if not name:
        return ""
    if str(club_setting(tenant, "locale") or "de").startswith("de"):
        return f"Der Hubra-Bot von {name}."
    return f"The Hubra bot for {name}."


def _fingerprint(profile: dict) -> str:
    raw = "|".join(str(profile.get(k) or "") for k in ("nick", "avatar", "bio"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def apply(conn, tenant, *, force: bool = False) -> list[str] | None:
    """Set nickname, avatar and bio on the server.

    Returns None when nothing changed since the last time (no call made),
    else the problems (empty when all went through). Raises DiscordError
    only when the server can't be reached at all."""
    profile = desired(conn, tenant)
    mark = _fingerprint(profile)
    if not force and (conn.config or {}).get("profile_applied") == mark:
        return None
    path = f"/guilds/{conn.external_id}/members/@me"
    problems: list[str] = []
    reason = "Hubra: club look"
    try:
        api.bot("PATCH", path, {"nick": profile["nick"]}, reason=reason)
    except DiscordError as exc:
        if exc.status != 403:
            raise
        problems.append(
            "The bot may not change its nickname: give the Hubra role "
            "'Change Nickname' in the server settings"
        )
    try:
        api.bot(
            "PATCH",
            path,
            {"avatar": profile["avatar"], "bio": profile["bio"]},
            reason=reason,
        )
    except DiscordError as exc:
        if exc.status not in (400, 403):
            raise
        problems.append(f"Discord refused the logo or bio: {exc}")
    if not problems:
        config = dict(conn.config or {})
        config["profile_applied"] = mark
        conn.config = config
    return problems


def brand(body: dict, tenant, config: dict) -> dict:
    """The announcement with the club's colour, name and logo."""
    if not config.get("club_embeds", True) or not body.get("embeds"):
        return body
    color = club_color(tenant)
    author = {"name": club_name(tenant)[:256]}
    icon = logo_url(tenant)
    if icon:
        author["icon_url"] = icon
    embeds = []
    for embed in body["embeds"]:
        embed = dict(embed)
        if color is not None:
            embed["color"] = color
        if author["name"]:
            embed["author"] = author
        embeds.append(embed)
    return {**body, "embeds": embeds}
