"""The Discord connected app: adding the Hubra bot to a club's server.

Hubra core runs the round trip (signed state, browser cookie, the admin's
confirmation, encrypted storage, audit). This file only knows Discord.
"""

from __future__ import annotations

import logging
import urllib.parse

from hubra.core import apps

from . import api
from .api import DiscordError

_log = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "channel_id": None,
    "announce_events": True,
    "announce_posts": True,
    "announce_polls": False,
    # Members-only events are left out unless the club says the channel is
    # for members only.
    "members_only_events": False,
    "mention": "",
    "role_map": [],
}
MAX_ROLE_MAP = 50


def _permissions(scopes) -> int:
    bits = 0
    if "announce" in scopes:
        bits |= api.VIEW_CHANNEL | api.SEND_MESSAGES | api.EMBED_LINKS
    if "commands" in scopes:
        bits |= api.VIEW_CHANNEL | api.SEND_MESSAGES | api.EMBED_LINKS
    if "roles" in scopes:
        bits |= api.MANAGE_ROLES
    return bits


class DiscordApp:
    def unavailable(self):
        missing = api.missing_settings()
        if missing:
            return "Discord isn't set up on this platform yet"
        return None

    def authorize_url(self, state, redirect_uri, scopes):
        query = urllib.parse.urlencode(
            {
                "client_id": api.config("DISCORD_CLIENT_ID"),
                "scope": "bot applications.commands",
                "permissions": str(_permissions(scopes)),
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "state": state,
                "integration_type": "0",
            }
        )
        return f"https://discord.com/oauth2/authorize?{query}"

    def complete(self, args, redirect_uri):
        """Which server the admin added the bot to.

        Read from Discord's answer to the code, not from the URL's
        ``guild_id``, which anyone could edit."""
        code = args.get("code")
        if not code:
            raise apps.AppError("Discord sent no code")
        try:
            token = api.form(
                "/oauth2/token",
                {
                    "client_id": api.config("DISCORD_CLIENT_ID"),
                    "client_secret": api.config("DISCORD_CLIENT_SECRET"),
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
            )
        except DiscordError as exc:
            raise apps.AppError(str(exc)) from exc
        guild = (token or {}).get("guild") or {}
        self._revoke(token)
        if not guild.get("id"):
            raise apps.AppError("Discord didn't say which server")
        config = dict(DEFAULT_CONFIG)
        config["guild_name"] = str(guild.get("name", ""))[:100]
        return apps.ConnectionDraft(
            external_id=str(guild["id"]),
            label=str(guild.get("name") or "Discord")[:200],
            config=config,
        )

    def _revoke(self, token):
        access = (token or {}).get("access_token")
        if not access:
            return
        try:  # only the server was needed; don't leave the token valid
            api.form(
                "/oauth2/token/revoke",
                {
                    "client_id": api.config("DISCORD_CLIENT_ID"),
                    "client_secret": api.config("DISCORD_CLIENT_SECRET"),
                    "token": access,
                    "token_type_hint": "access_token",
                },
            )
        except DiscordError as exc:
            _log.info("discord: could not revoke the add-to-server token: %s", exc)

    def confirmed(self, conn):
        from .hooks import after_commit
        from .tasks import sync_connection

        if apps.has_scope(conn, "roles"):
            after_commit(sync_connection.delay, conn.id)
        if apps.has_scope(conn, "commands"):
            from .commands import register_after_commit

            register_after_commit(conn)

    def options(self, conn):
        """Channels and roles on the server, and what the club can map."""
        from .roles import sources

        guild = conn.external_id
        try:
            channels = [
                {"id": c["id"], "name": c["name"]}
                for c in api.channels(guild)
                if c.get("type") in api.TEXT_CHANNELS
            ]
            all_roles = api.roles(guild)
            assignable = {r["id"] for r in api.assignable_roles(guild)}
        except DiscordError as exc:
            raise apps.AppError(_explain(exc)) from exc
        roles = [
            {"id": r["id"], "name": r["name"], "assignable": r["id"] in assignable}
            for r in sorted(all_roles, key=lambda r: -r["position"])
            if r["id"] != guild and not r.get("managed")
        ]
        return {"channels": channels, "roles": roles, "sources": sources()}

    def update_config(self, conn, config):
        from .roles import sources

        out = dict(DEFAULT_CONFIG)
        out.update({k: v for k, v in (conn.config or {}).items() if k in out})
        out["guild_name"] = (conn.config or {}).get("guild_name", "")
        for key in (
            "announce_events",
            "announce_posts",
            "announce_polls",
            "members_only_events",
        ):
            if key in config:
                out[key] = bool(config[key])

        needs_server = (
            "channel_id" in config
            or "role_map" in config
            or (str(config.get("mention", "")).startswith("role:"))
        )
        try:
            server_channels = (
                {
                    c["id"]
                    for c in api.channels(conn.external_id)
                    if c.get("type") in api.TEXT_CHANNELS
                }
                if needs_server
                else set()
            )
            server_roles = (
                {r["id"] for r in api.roles(conn.external_id)}
                if needs_server
                else set()
            )
            assignable = (
                {r["id"] for r in api.assignable_roles(conn.external_id)}
                if "role_map" in config
                else set()
            )
        except DiscordError as exc:
            raise apps.AppError(_explain(exc)) from exc

        if "channel_id" in config:
            channel = config["channel_id"] or None
            if channel is not None and str(channel) not in server_channels:
                raise apps.AppError("That channel isn't a text channel on the server")
            out["channel_id"] = str(channel) if channel else None

        if "mention" in config:
            mention = str(config["mention"] or "")
            if mention.startswith("role:"):
                if mention[5:] not in server_roles:
                    raise apps.AppError("That role isn't on the server")
            elif mention not in ("", "everyone", "here"):
                raise apps.AppError("Mention must be none, @everyone, @here or a role")
            out["mention"] = mention

        if "role_map" in config:
            entries = config["role_map"]
            if not isinstance(entries, list) or len(entries) > MAX_ROLE_MAP:
                raise apps.AppError(f"Up to {MAX_ROLE_MAP} role mappings")
            known = {s["key"] for s in sources()}
            seen, clean = set(), []
            for entry in entries:
                if not isinstance(entry, dict):
                    raise apps.AppError("Bad role mapping")
                source = str(entry.get("source", ""))
                role = str(entry.get("role_id", ""))
                if source not in known:
                    raise apps.AppError(f"Unknown group, function or tier: {source}")
                if role not in assignable:
                    raise apps.AppError(
                        "The bot can only give roles below its own role; "
                        "move the Hubra role up in the server settings"
                    )
                if (source, role) not in seen:
                    seen.add((source, role))
                    clean.append({"source": source, "role_id": role})
            if clean != out.get("role_map") and apps.has_scope(conn, "roles"):
                from .hooks import after_commit
                from .tasks import sync_connection

                after_commit(sync_connection.delay, conn.id)
            out["role_map"] = clean
        return out

    def test(self, conn):
        channel = (conn.config or {}).get("channel_id")
        if not channel:
            raise apps.AppError("Pick a channel first")
        try:
            api.send(
                channel,
                {
                    "embeds": [
                        {
                            "title": "✅ Hubra is connected",
                            "description": "Announcements will appear here.",
                            "color": 0x6D3FD8,
                        }
                    ],
                    "allowed_mentions": {"parse": []},
                },
            )
        except DiscordError as exc:
            raise apps.AppError(_explain(exc)) from exc
        return "Test message sent"

    def revoke(self, conn):
        try:
            api.leave(conn.external_id)
        except DiscordError as exc:
            if exc.status != 404:  # already gone from the server
                raise


def _explain(exc: DiscordError) -> str:
    if exc.status == 403:
        return "The bot lacks a permission on the server (channel or role access)"
    if exc.status == 404:
        return "The server, channel or role is gone, or the bot was removed"
    return str(exc)


handler = DiscordApp()
