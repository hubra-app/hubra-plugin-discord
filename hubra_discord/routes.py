"""Discord blueprint: /api/v1/discord (send a test message)."""

from __future__ import annotations

from flask import Blueprint, jsonify

from hubra.api.permissions import permission_required
from hubra.core.errors import api_error
from hubra.core.modules import get_config

from .webhook import WebhookError, embed, is_webhook_url, payload, send

discord_bp = Blueprint("discord_bp", __name__)


@discord_bp.post("/test")
@permission_required("discord", "manage", "Configure Discord")
def send_test():
    """Post a test message right away, so admins see the setup works."""
    url = get_config("discord").get("webhook_url")
    if not is_webhook_url(url):
        return api_error(
            "DISCORD_NOT_CONFIGURED",
            "Set a Discord webhook URL (https://discord.com/api/webhooks/…) first",
            400,
        )
    try:
        send(
            url,
            payload(None, [embed("✅ Connected", "Announcements will appear here.")]),
        )
    except WebhookError as exc:
        return api_error("DISCORD_FAILED", str(exc), 502)
    return jsonify({"ok": True}), 200
