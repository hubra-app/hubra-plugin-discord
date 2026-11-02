"""Discord blueprint: /api/v1/discord/interactions.

Discord calls this for every slash command and button click on any server
the Hubra bot is on. Nothing is trusted before the Ed25519 signature with
the application's public key checks out. Clicks and commands for the
platform's own server go to core's platform bot; otherwise the server must
be connected to a club (``hubra.core.apps``) that has the Discord module on.
"""

from __future__ import annotations

import json
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from flask import Blueprint, jsonify, request

from hubra.core import apps
from hubra.core.extensions import db

from . import api
from .commands import handle, reply

discord_bp = Blueprint("discord_bp", __name__)

# Signed requests older than this are refused (replays).
MAX_AGE = 300


PAUSED = "Hubra wird gerade aktualisiert. Bitte versuche es in ein paar Minuten wieder."


def _club_paused(tenant_id: int) -> bool:
    """A maintenance window just for this club (or for some clubs) is on."""
    try:
        from hubra.core.services import maintenance
    except ImportError:
        return False
    club_state = getattr(maintenance, "club_state", None)
    if club_state is None:  # Hubra before maintenance windows
        return False
    try:
        return bool(club_state(tenant_id)["active"])
    except Exception:  # noqa: BLE001  never lock a club out over this check
        return False


def verified(body: bytes, signature: str, timestamp: str) -> bool:
    key = api.config("public_key")
    if not key or not signature or not timestamp:
        return False
    try:
        if abs(time.time() - int(timestamp)) > MAX_AGE:
            return False
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(key)).verify(
            bytes.fromhex(signature), timestamp.encode() + body
        )
    except (InvalidSignature, ValueError):
        return False
    return True


@discord_bp.post("/interactions")
def interactions():
    from hubra.core.models.tenant import Tenant
    from hubra.core.modules import is_enabled
    from hubra.core.tenant.context import in_club

    body = request.get_data()
    if not verified(
        body,
        request.headers.get("X-Signature-Ed25519", ""),
        request.headers.get("X-Signature-Timestamp", ""),
    ):
        return jsonify({"error": "invalid request signature"}), 401
    try:
        interaction = json.loads(body)
    except ValueError:
        return jsonify({"error": "bad body"}), 400

    if interaction.get("type") == 1:  # Discord checking the endpoint
        return jsonify({"type": 1}), 200

    # The platform's own server: Hubra's admins (core's platform bot).
    try:
        from hubra.core.services import platform_bot
    except ImportError:  # an older Hubra without it
        platform_bot = None
    if platform_bot is not None and platform_bot.is_platform_interaction(interaction):
        return jsonify(platform_bot.handle(interaction)), 200

    # Interactions pass the maintenance gate (so /wartung ende works);
    # club commands wait until the update is done.
    try:
        from hubra.core.services import maintenance

        if maintenance.is_active():
            return jsonify(reply(PAUSED)), 200
    except ImportError:
        pass

    guild_id = interaction.get("guild_id")
    conn = apps.active("discord", guild_id) if guild_id else None
    tenant = db.session.get(Tenant, conn.tenant_id) if conn is not None else None
    if conn is None or tenant is None or tenant.status != "active":
        return jsonify(
            reply("Dieser Server ist mit keinem Verein in Hubra verbunden.")
        ), 200
    if _club_paused(tenant.id):
        return jsonify(reply(PAUSED)), 200
    with in_club(tenant):
        if not is_enabled("discord"):
            return jsonify(reply("Discord ist für diesen Verein ausgeschaltet.")), 200
        return jsonify(handle(conn, interaction)), 200
