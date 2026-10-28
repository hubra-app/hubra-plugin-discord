"""Discord: announce new events, posts and polls in a Discord channel."""

from hubra.core.modules import Blueprint, Module, SettingField
from hubra.core.modules.events import (
    EVENT_PUBLISHED,
    POLL_PUBLISHED,
    POST_PUBLISHED,
)

_HOOKS = "hubra_discord.hooks"

module = Module(
    code="discord",
    name="Discord",
    description="Announce new events, posts and polls in a Discord channel",
    default_enabled=False,
    depends_on=("members",),
    blueprints=(
        Blueprint(
            "hubra_discord.routes",
            "discord_bp",
            "/api/v1/discord",
            v2=(("/api/v2/tenants/discord", "discord_bp_v2"),),
        ),
    ),
    settings=(
        SettingField(
            "webhook_url",
            "secret",
            label="Webhook URL",
            description="Discord channel settings > Integrations > Webhooks > Copy URL",
            required=True,
        ),
        SettingField("announce_events", "bool", default=True, label="New events"),
        SettingField("announce_posts", "bool", default=True, label="New posts"),
        SettingField("announce_polls", "bool", default=False, label="New polls"),
        SettingField(
            "mention",
            "str",
            default="",
            label="Mention",
            description="Optional, e.g. @everyone or <@&role id>",
        ),
    ),
    events={
        EVENT_PUBLISHED: f"{_HOOKS}:on_event_published",
        POST_PUBLISHED: f"{_HOOKS}:on_post_published",
        POLL_PUBLISHED: f"{_HOOKS}:on_poll_published",
    },
    tasks=("hubra_discord.tasks",),
)
