"""Discord blueprint: /api/v1/discord/interactions.

Discord calls this for every slash command and button click on any server
the Hubra bot is on. Nothing is trusted before the Ed25519 signature with
the application's public key checks out; then the server must be connected
to a club (``hubra.core.apps``) that has the Discord module on.
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


def verified(body: bytes, signature: str, timestamp: str) -> bool:
    key = api.config("DISCORD_PUBLIC_KEY")
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

    guild_id = interaction.get("guild_id")
    conn = apps.active("discord", guild_id) if guild_id else None
    tenant = db.session.get(Tenant, conn.tenant_id) if conn is not None else None
    if conn is None or tenant is None or tenant.status != "active":
        return jsonify(
            reply("Dieser Server ist mit keinem Verein in Hubra verbunden.")
        ), 200
    with in_club(tenant):
        if not is_enabled("discord"):
            return jsonify(reply("Discord ist für diesen Verein ausgeschaltet.")), 200
        return jsonify(handle(conn, interaction)), 200
