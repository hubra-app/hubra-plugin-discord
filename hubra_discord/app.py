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

# What the bot posts, each to its own channel if the club picks one, else to
# the main channel (channel_id). ``onsite``: Booking's "heute vor Ort" list
# (onsite.py). ``changelog``: new Hubra and plugin versions
# (changelog.py), e.g. an announcements or a dedicated changelog channel.
KINDS = (
    "events",
    "posts",
    "polls",
    "reminders",
    "public_events",
    "changelog",
    "onsite",
)

DEFAULT_CONFIG = {
    "channel_id": None,
    "channels": {},
    "announce_events": True,
    "announce_posts": True,
    "announce_polls": False,
    # Event reminders and "poll closes soon" from the club's reminder job.
    "announce_reminders": False,
    # Members-only events are left out unless the club says the channel is
    # for members only.
    "members_only_events": False,
    "mention": "",
    "role_map": [],
    # The bot as the club on this server (nickname, logo, bio), and the
    # club's colour, name and logo on announcements. See profile.py.
    "club_profile": True,
    "club_embeds": True,
    # Buttons to vote under poll announcements (``commands`` scope).
    "poll_buttons": True,
    # The club's events as the server's events (``events`` scope).
    "discord_events": True,
    # Private channels per group (``channels`` scope): [{group_id,
    # channel_id, role_id, name}]; their group's events and polls go there.
    "group_channels": [],
    "group_announce": True,
    # Direct messages (``notify`` scope): a welcome for new people on the
    # server (needs the gateway process), with an optional own text.
    "welcome_dm": False,
    "welcome_text": "",
    # /trainings: members check in and excuse themselves.
    "self_checkin": True,
    # New Hubra versions and new versions of the club's plugins, with their
    # changelog, in the changelog channel (else the main channel); see
    # changelog.py. Off by default: no club gets them without opting in.
    "hubra_news": False,
    "plugin_news": False,
    # Hubra's planned or running maintenance in the main channel (platform.py).
    "maintenance_notices": False,
    # A role for every open or visible group (``roles`` scope), made and
    # kept up by the bot (group_roles.py).
    "group_roles": False,
    # The bot log on the club's own server (botlog.py): a channel, and
    # whether it gets only errors or everything the bot does and skips.
    "log_channel_id": None,
    "log_level": "errors",
    # The "closes soon" post of a required poll mentions the linked
    # members who haven't voted yet (nudge.py).
    "nudge_voters": False,
    # One role for every member now (member_role.py), for members-only
    # channels; its name.
    "member_role": False,
    "member_role_name": "",
    # A training of a group with its own channel gets a card there before it
    # starts, with "Bin da" / "Kann nicht" and the count (training.py).
    "training_posts": True,
    # A locked voice channel whose name shows the member count (stats.py),
    # renamed every night; its name pattern with {n}.
    "stats_channel": False,
    "stats_name": "",
    # Monday morning: the week's events in the events channel (digest.py),
    # and the club's numbers for the admins in the moderation channel.
    "weekly_events": False,
    "weekly_stats": False,
    # Moderation (``moderate`` scope, mod.py, leavers.py): reports and new
    # people go to this channel (else the bot log channel).
    "mod_channel_id": None,
    "reports": True,
    # Someone who isn't a linked member joins: the admins get a card to let
    # them in as a guest (a role) or remove them.
    "guest_queue": False,
    "guest_role_name": "",
    # People who left the club are removed from the server (nightly).
    "kick_leavers": False,
    # After an event: "Wie war es?" with 1-5 stars for those who came
    # (feedback.py).
    "event_feedback": False,
    # Booking's Vor Ort: in the morning, who said they'll be in which room
    # today (onsite.py). It names members, so off by default.
    "onsite_today": False,
}
# Kept by the bot, not set by the club.
INTERNAL = (
    "guild_name",
    "profile_applied",
    "scheduled_events",
    "auto_group_roles",
    "posted",
    "made_channels",
    "auto_member_role",
    # Public channels a club admin approved (audit.py): [{id, by, at}].
    "public_ok",
    # The channels the layout made (layout.py): {slot: channel id}.
    "layout",
    "audit_seen",
    # stats.py, digest.py, mod.py, leavers.py, feedback.py
    "stats_channel_id",
    "weekly_sent",
    "guest_role",
    "kicked",
    "feedback",
    # changelog.py: versions and delivery ids posted already.
    "changelog_sent",
)
BOOLS = (
    "announce_events",
    "announce_posts",
    "announce_polls",
    "announce_reminders",
    "members_only_events",
    "club_profile",
    "club_embeds",
    "poll_buttons",
    "discord_events",
    "group_announce",
    "welcome_dm",
    "self_checkin",
    "hubra_news",
    "plugin_news",
    "maintenance_notices",
    "group_roles",
    "nudge_voters",
    "member_role",
    "training_posts",
    "stats_channel",
    "weekly_events",
    "weekly_stats",
    "reports",
    "guest_queue",
    "kick_leavers",
    "event_feedback",
    "onsite_today",
)
# Switches that need a scope the connection may not have.
NEEDS_SCOPE = {
    "stats_channel": "channels",
    "kick_leavers": "moderate",
    "guest_queue": "moderate",
    "event_feedback": "commands",
}
MAX_ROLE_MAP = 50


def _permissions(scopes) -> int:
    bits = api.CHANGE_NICKNAME if scopes else 0
    if "announce" in scopes:
        bits |= api.VIEW_CHANNEL | api.SEND_MESSAGES | api.EMBED_LINKS
    if "commands" in scopes:
        bits |= api.VIEW_CHANNEL | api.SEND_MESSAGES | api.EMBED_LINKS
    if "roles" in scopes:
        bits |= api.MANAGE_ROLES
    if "events" in scopes:
        bits |= api.CREATE_EVENTS
    if "channels" in scopes:
        bits |= (
            api.MANAGE_CHANNELS
            | api.MANAGE_ROLES
            | api.VIEW_CHANNEL
            | api.SEND_MESSAGES
            | api.EMBED_LINKS
            | api.READ_MESSAGE_HISTORY
        )
    if "moderate" in scopes:
        bits |= (
            api.KICK_MEMBERS
            | api.MANAGE_MESSAGES
            | api.MANAGE_ROLES
            | api.VIEW_CHANNEL
            | api.SEND_MESSAGES
            | api.EMBED_LINKS
            | api.READ_MESSAGE_HISTORY
        )
    return bits


def grant_url(conn) -> str:
    """Add the bot again with what the connection's scopes need now: Discord
    updates the bot's role on that server (for scopes turned on later)."""
    query = urllib.parse.urlencode(
        {
            "client_id": api.config("client_id"),
            "scope": "bot applications.commands",
            "permissions": str(_permissions(conn.scopes or [])),
            "guild_id": conn.external_id,
            "disable_guild_select": "true",
            "integration_type": "0",
        }
    )
    return f"https://discord.com/oauth2/authorize?{query}"


def missing_permissions(conn) -> list[str]:
    """What the scopes need that the bot lacks on the server."""
    need = _permissions(conn.scopes or [])
    have = api.guild_permissions(conn.external_id)
    if have & api.ADMINISTRATOR:
        return []
    return [
        name
        for bit, name in api.PERMISSION_NAMES.items()
        if need & bit and not have & bit
    ]


def used_channels(config: dict) -> dict[str, list[str]]:
    """Channel id → what the bot posts there (main, events, ..., log)."""
    used: dict[str, list[str]] = {}
    if config.get("channel_id"):
        used.setdefault(str(config["channel_id"]), []).append("main")
    for kind, channel in (config.get("channels") or {}).items():
        if channel:
            used.setdefault(str(channel), []).append(kind)
    if config.get("log_channel_id"):
        used.setdefault(str(config["log_channel_id"]), []).append("log")
    if config.get("mod_channel_id"):
        used.setdefault(str(config["mod_channel_id"]), []).append("mod")
    return used


def channel_problems(conn) -> list[dict]:
    """Per channel the bot posts in: the permissions it lacks there (the
    channel's own overrides can take away what the server gives)."""
    from .diagnose import missing_in_channel

    out = []
    for channel, uses in used_channels(conn.config or {}).items():
        try:
            found = missing_in_channel(conn.external_id, channel)
        except DiscordError as exc:
            _log.info("discord: checking channel %s: %s", channel, exc)
            continue
        if found is None:
            out.append(
                {"channel": f"#{channel}", "uses": uses, "missing": ["channel deleted"]}
            )
        elif found[1]:
            out.append({"channel": found[0], "uses": uses, "missing": found[1]})
    return out


def channel_for(config: dict, kind: str) -> str | None:
    """The channel *kind* goes to: its own, else the main channel."""
    return (config.get("channels") or {}).get(kind) or config.get("channel_id")


class DiscordApp:
    def unavailable(self):
        missing = api.missing_settings()
        if missing:
            return "Discord isn't set up on this platform yet"
        return None

    def authorize_url(self, state, redirect_uri, scopes):
        query = urllib.parse.urlencode(
            {
                "client_id": api.config("client_id"),
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
                    "client_id": api.config("client_id"),
                    "client_secret": api.config("client_secret"),
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
                    "client_id": api.config("client_id"),
                    "client_secret": api.config("client_secret"),
                    "token": access,
                    "token_type_hint": "access_token",
                },
            )
        except DiscordError as exc:
            _log.info("discord: could not revoke the add-to-server token: %s", exc)

    def confirmed(self, conn):
        from .hooks import after_commit
        from .tasks import apply_profile, sync_connection

        after_commit(apply_profile.delay, conn.id)

        if apps.has_scope(conn, "roles"):
            after_commit(sync_connection.delay, conn.id)
        if apps.has_scope(conn, "events"):
            from .tasks import sync_events

            after_commit(sync_events.delay, conn.id)
        if apps.has_scope(conn, "commands"):
            from .commands import register_after_commit

            register_after_commit(conn)

    def options(self, conn):
        """Channels and roles on the server, what the club can map, and the
        permissions the bot still lacks for the scopes."""
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
            missing = missing_permissions(conn)
        except DiscordError as exc:
            raise apps.AppError(_explain(exc)) from exc
        roles = [
            {"id": r["id"], "name": r["name"], "assignable": r["id"] in assignable}
            for r in sorted(all_roles, key=lambda r: -r["position"])
            if r["id"] != guild and not r.get("managed")
        ]
        return {
            "channels": channels,
            "roles": roles,
            "sources": sources(),
            "missingPermissions": missing,
            "channelProblems": channel_problems(conn),
            "grantUrl": grant_url(conn),
        }

    def deliver_notification(self, conn, external_id, payload):
        from .notify import deliver

        return deliver(conn, external_id, payload)

    def platform_notice(self, conn, kind, data):
        from .platform import platform_notice

        return platform_notice(conn, kind, data)

    def update_config(self, conn, config):
        from .roles import sources

        out = dict(DEFAULT_CONFIG)
        out.update({k: v for k, v in (conn.config or {}).items() if k in out})
        for key in INTERNAL:
            if key in (conn.config or {}):
                out[key] = (conn.config or {})[key]
        out.setdefault("guild_name", "")
        for key in BOOLS:
            if key in config:
                out[key] = bool(config[key])
        for key, scope in NEEDS_SCOPE.items():
            if config.get(key) and not apps.has_scope(conn, scope):
                raise apps.AppError(f"Turn on «{scope}» for this connection first")
        for key, limit in (("stats_name", 90), ("guest_role_name", 100)):
            if key in config:
                out[key] = " ".join(str(config[key] or "").split())[:limit]
        if out["stats_name"] and "{n}" not in out["stats_name"]:
            raise apps.AppError("The stats channel name needs {n} for the number")
        if "stats_channel" in config or "stats_name" in config:
            from .hooks import after_commit
            from .tasks import daily_extras

            after_commit(daily_extras.delay, conn.id)
        if "group_roles" in config:
            if out["group_roles"] and not apps.has_scope(conn, "roles"):
                raise apps.AppError("Turn on roles for this connection first")
            if out["group_roles"] != bool((conn.config or {}).get("group_roles")):
                from .hooks import after_commit
                from .tasks import sync_connection

                after_commit(sync_connection.delay, conn.id)  # make or delete
        if "member_role_name" in config:
            out["member_role_name"] = str(config["member_role_name"] or "").strip()[
                :100
            ]
        if "member_role" in config or "member_role_name" in config:
            if out["member_role"] and not (
                apps.has_scope(conn, "roles") or apps.has_scope(conn, "channels")
            ):
                raise apps.AppError("Turn on roles for this connection first")
            if out["member_role"] != bool(
                (conn.config or {}).get("member_role")
            ) or out["member_role_name"] != (conn.config or {}).get(
                "member_role_name", ""
            ):
                from .hooks import after_commit
                from .tasks import sync_connection

                after_commit(sync_connection.delay, conn.id)
        if "welcome_text" in config:
            text = str(config["welcome_text"] or "").strip()
            from .notify import MAX_WELCOME

            if len(text) > MAX_WELCOME:
                raise apps.AppError(
                    f"The welcome text is limited to {MAX_WELCOME} characters"
                )
            out["welcome_text"] = text
        if (
            "discord_events" in config or "members_only_events" in config
        ) and apps.has_scope(conn, "events"):
            from .hooks import after_commit
            from .tasks import sync_events

            after_commit(sync_events.delay, conn.id)
        if "club_profile" in config:
            from .hooks import after_commit
            from .tasks import apply_profile

            # Also picks up a new club name or logo; nothing is sent when
            # nothing changed.
            after_commit(apply_profile.delay, conn.id)

        needs_server = (
            "channel_id" in config
            or "log_channel_id" in config
            or "mod_channel_id" in config
            or "channels" in config
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

        if "log_channel_id" in config:
            channel = config["log_channel_id"] or None
            if channel is not None and str(channel) not in server_channels:
                raise apps.AppError("That channel isn't a text channel on the server")
            out["log_channel_id"] = str(channel) if channel else None

        if "mod_channel_id" in config:
            channel = config["mod_channel_id"] or None
            if channel is not None and str(channel) not in server_channels:
                raise apps.AppError("That channel isn't a text channel on the server")
            out["mod_channel_id"] = str(channel) if channel else None

        if "log_level" in config:
            from .botlog import LEVELS

            if config["log_level"] not in LEVELS:
                raise apps.AppError("The log gets errors or all")
            out["log_level"] = config["log_level"]

        if "channel_id" in config:
            channel = config["channel_id"] or None
            if channel is not None and str(channel) not in server_channels:
                raise apps.AppError("That channel isn't a text channel on the server")
            out["channel_id"] = str(channel) if channel else None

        if "channels" in config:
            picked = config["channels"]
            if not isinstance(picked, dict) or set(picked) - set(KINDS):
                raise apps.AppError(f"Channels are set per {', '.join(KINDS)}")
            clean = {}
            for kind, channel in picked.items():
                if not channel:
                    continue  # the main channel
                if str(channel) not in server_channels:
                    raise apps.AppError(
                        "That channel isn't a text channel on the server"
                    )
                clean[kind] = str(channel)
            out["channels"] = clean

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

        if "group_channels" in config:
            if not apps.has_scope(conn, "channels"):
                raise apps.AppError("Turn on group channels for this connection first")
            from .groups import update as update_groups

            entries = update_groups(
                conn, config["group_channels"], out.get("role_map") or []
            )
            if entries != out.get("group_channels"):
                from .hooks import after_commit
                from .tasks import sync_connection

                after_commit(sync_connection.delay, conn.id)  # roles for the channels
            out["group_channels"] = entries
        return out

    def test(self, conn):
        """Post a test message in every channel the bot uses, and say per
        channel what works and what's missing."""
        from .diagnose import describe

        used = used_channels(conn.config or {})
        if not used:
            raise apps.AppError("Pick a channel first")
        ok, failed = [], []
        for channel, kinds in used.items():
            try:
                api.send(
                    channel,
                    {
                        "embeds": [
                            {
                                "title": "✅ Hubra is connected",
                                "description": "This channel gets: " + ", ".join(kinds),
                                "color": 0x6D3FD8,
                            }
                        ],
                        "allowed_mentions": {"parse": []},
                    },
                )
                ok.append(f"<#{channel}> ({', '.join(kinds)})")
            except DiscordError as exc:
                failed.append(
                    describe(
                        exc,
                        doing=f"Test for {', '.join(kinds)}",
                        guild_id=conn.external_id,
                        channel_id=channel,
                    )
                )
        missing = []
        try:
            missing = missing_permissions(conn)
        except DiscordError:
            pass
        if missing:
            failed.append(
                "On the server the bot lacks "
                + ", ".join(missing)
                + "; add the bot again with the link under Details"
            )
        # Hubra records the result in the activity list.
        if failed:
            raise apps.AppError("; ".join(failed)[:500])
        return f"Test sent to {', '.join(ok)}"

    def revoke(self, conn):
        from .scheduled import remove_all

        try:
            remove_all(conn)  # the bot's events would stay behind
        except DiscordError as exc:
            _log.info("discord: could not remove the events: %s", exc)
        try:
            api.leave(conn.external_id)
        except DiscordError as exc:
            if exc.status != 404:  # already gone from the server
                raise


def _explain(exc: DiscordError) -> str:
    """For answers in the settings (no channel known): Discord's own code
    and the call, never just "no permission"."""
    from .diagnose import describe

    return describe(exc)


handler = DiscordApp()
