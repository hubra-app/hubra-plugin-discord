"""Discord: the Hubra bot on a club's Discord server.

A club connects its server under Einstellungen › Verbundene Apps. The bot
then announces new events, posts and polls, keeps Discord roles in line
with the club's groups, functions and tiers, and lets linked members sign
up for events and use slash commands.
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
    GROUP_MEMBERSHIP_CHANGED,
    POLL_PUBLISHED,
    POST_PUBLISHED,
)

_HOOKS = "hubra_discord.hooks"

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
                    "Post new events, posts and polls in a channel",
                    "Members-only events and polls for some groups stay out.",
                ),
                AppScope(
                    "roles",
                    "Give and take Discord roles that follow groups, functions and tiers",
                    "Only roles you map; only for members who linked their Discord account.",
                ),
                AppScope(
                    "commands",
                    "Let linked members sign up for events and use /events",
                    "Same rules as in the app.",
                ),
            ),
        ),
    ),
    events={
        EVENT_PUBLISHED: f"{_HOOKS}:on_event_published",
        POST_PUBLISHED: f"{_HOOKS}:on_post_published",
        POLL_PUBLISHED: f"{_HOOKS}:on_poll_published",
        GROUP_MEMBERSHIP_CHANGED: f"{_HOOKS}:on_group_membership_changed",
        ACCOUNT_LINKED: f"{_HOOKS}:on_account_linked",
        ACCOUNT_UNLINKED: f"{_HOOKS}:on_account_unlinked",
    },
    tasks=("hubra_discord.tasks",),
    schedule=(
        Schedule(
            "discord-role-sync",
            "hubra_discord.tasks.sync_all_clubs",
            every=24 * 3600,
        ),
    ),
)
