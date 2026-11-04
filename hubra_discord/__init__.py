"""Discord: the Hubra bot on a club's Discord server.

A club connects its server under Einstellungen › Verbundene Apps. The bot
then announces new events, posts and polls, keeps Discord roles in line
with the club's groups, functions and tiers, lets linked members sign up,
vote and check in with buttons and slash commands, shows the club's events
in the server's Events list, keeps private channels per group, and sends
members their notifications as direct messages when they ask for it.
"""

from hubra.core.modules import (
    AppProvider,
    AppScope,
    Blueprint,
    Module,
    Schedule,
)
from hubra.core.modules.events import (
    ACCOUNT_LINKED,
    ACCOUNT_UNLINKED,
    EVENT_PUBLISHED,
    EVENT_REMINDER,
    GROUP_MEMBERSHIP_CHANGED,
    POLL_CLOSING,
    POLL_PUBLISHED,
    POST_PUBLISHED,
)

_HOOKS = "hubra_discord.hooks"
# Hubra versions before event.changed and module processes still load the
# plugin; they just never send the event or run the gateway.
EVENT_CHANGED = "event.changed"
# Newer Hubra sends these; older versions never do, and the messages then
# catch up on the next change or at tidy_messages.
EVENT_SIGNUPS_CHANGED = "event.signups_changed"
POLL_CLOSED = "poll.closed"
_EXTRA = (
    {"processes": {"discord-gateway": "hubra_discord.gateway:run"}}
    if "processes" in getattr(Module, "__dataclass_fields__", {})
    else {}
)

module = Module(
    code="discord",
    name="Discord",
    description="The Hubra bot on your Discord server: announcements, roles, sign-ups",
    default_enabled=False,
    depends_on=("members",),
    blueprints=(Blueprint("hubra_discord.routes", "discord_bp", "/api/v1/discord"),),
    # Discord calls it for every server; it finds the club itself.
    public_endpoints=("discord_bp.interactions",),
    apps=(
        AppProvider(
            code="discord",
            name="Discord",
            handler="hubra_discord.app:handler",
            icon="brand-discord",
            description=(
                "Add the Hubra bot to your Discord server: announcements, "
                "roles that follow your groups, and sign-ups from Discord."
            ),
            scopes=(
                AppScope(
                    "announce",
                    "Post new events, posts, polls and reminders in your channels",
                    "Members-only events and polls for some groups stay out.",
                ),
                AppScope(
                    "roles",
                    "Give and take Discord roles that follow groups, functions and tiers",
                    "Only roles you map; only for members who linked their Discord account.",
                ),
                AppScope(
                    "commands",
                    "Let linked members sign up for events, vote on polls and use "
                    "/events, /invoices, /trainings and /profile",
                    "Same rules as in the app; answers only the person sees.",
                ),
                AppScope(
                    "events",
                    "Show the club's events in the server's Events list",
                    "Public events only, unless you say the server is for members.",
                ),
                AppScope(
                    "channels",
                    "Make a private channel for each group you pick",
                    "Only the group's members who linked Discord see it.",
                ),
                AppScope(
                    "moderate",
                    "Moderate: reports, new people and people who left the club",
                    "Removes people only when you say so; deletes only "
                    "messages an admin picks.",
                ),
                AppScope(
                    "notify",
                    "Send direct messages: notifications members ask for, "
                    "and a welcome for new people",
                    "Members choose per topic; nothing is sent unless they do.",
                ),
            ),
        ),
    ),
    events={
        EVENT_PUBLISHED: f"{_HOOKS}:on_event_published",
        EVENT_CHANGED: f"{_HOOKS}:on_event_changed",
        EVENT_SIGNUPS_CHANGED: f"{_HOOKS}:on_event_signups_changed",
        POLL_CLOSED: f"{_HOOKS}:on_poll_closed",
        POST_PUBLISHED: f"{_HOOKS}:on_post_published",
        POLL_PUBLISHED: f"{_HOOKS}:on_poll_published",
        EVENT_REMINDER: f"{_HOOKS}:on_event_reminder",
        POLL_CLOSING: f"{_HOOKS}:on_poll_closing",
        GROUP_MEMBERSHIP_CHANGED: f"{_HOOKS}:on_group_membership_changed",
        ACCOUNT_LINKED: f"{_HOOKS}:on_account_linked",
        ACCOUNT_UNLINKED: f"{_HOOKS}:on_account_unlinked",
    },
    tasks=("hubra_discord.tasks",),
    # processes: the gateway, only for "someone joined the server" (welcome,
    # roles on join).
    **_EXTRA,
    schedule=(
        Schedule(
            "discord-role-sync",
            "hubra_discord.tasks.sync_all_clubs",
            every=24 * 3600,
        ),
        # Polls that closed on their own show their result; events that
        # are over lose their buttons (live.py).
        # Monday's posts and feedback after events.
        Schedule(
            "discord-hourly",
            "hubra_discord.tasks.hourly",
            every=3600,
        ),
        Schedule(
            "discord-tidy-messages",
            "hubra_discord.tasks.tidy_messages",
            every=10 * 60,
        ),
    ),
)
