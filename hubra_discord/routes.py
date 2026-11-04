"""Discord blueprint: /api/v1/discord/interactions.

Discord calls this for every slash command and button click on any server
the Hubra bot is on. Nothing is trusted before the Ed25519 signature with
the application's public key checks out. Clicks and commands for the
platform's own server go to core's platform bot; otherwise the server must
be connected to a club (``hubra.core.apps``) that has the Discord module on.
"""

from __future__ import annotations

import json
import logging
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from flask import Blueprint, jsonify, request

from hubra.core import apps
from hubra.core.extensions import db

from . import api
from .commands import handle, reply

_log = logging.getLogger(__name__)

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
        return jsonify(_handle_logged(conn, interaction)), 200


def _what(interaction: dict) -> str:
    data = interaction.get("data") or {}
    user = (
        (interaction.get("member") or {}).get("user") or interaction.get("user") or {}
    )
    who = user.get("global_name") or user.get("username") or "someone"
    if interaction.get("type") == 2:
        return f"/{data.get('name', '?')} by {who}"
    action = str(data.get("custom_id", "")).partition(":")[0] or "button"
    return f"{action} button by {who}"


def _handle_logged(conn, interaction: dict) -> dict:
    """Run a command or button, and log it (and any crash) in the bot log."""
    from . import botlog

    what = _what(interaction)
    try:
        answer = handle(conn, interaction)
    except Exception as exc:  # noqa: BLE001  answer the person, log the cause
        db.session.rollback()
        _log.exception("discord: interaction failed: %s", what)
        botlog.log(
            conn, "command", False, f"{what} failed: {type(exc).__name__}: {exc}"
        )
        db.session.commit()
        return reply(
            "Das hat nicht geklappt. Der Fehler ist im Bot-Protokoll vermerkt."
        )
    if interaction.get("type") == 2:
        # Buttons log their own result (sign-up, vote, attendance).
        botlog.log(conn, "command", True, what)
        db.session.commit()
    return answer


# ── The club's server from Hubra (Settings › Verbundene Apps › Discord) ──────
#
# For club admins who manage connected apps (``apps.apps.manage``), like the
# connection's own settings.


def _manager():
    """The signed-in member, if they may manage connected apps; else None."""
    from flask_jwt_extended import verify_jwt_in_request

    from hubra.api.auth.helpers import get_current_member
    from hubra.api.permissions import member_has_permission, member_is_admin

    try:
        verify_jwt_in_request()
        member = get_current_member()
    except Exception:  # noqa: BLE001
        return None
    if member is None:
        return None
    if member_is_admin(member) or member_has_permission(member, "apps.apps.manage"):
        return member
    return None


def _club_connection(connection_id: int):
    """(member, connection) or an error response."""
    from hubra.core.errors import api_error
    from hubra.core.tenant.context import current_tenant

    member = _manager()
    if member is None:
        return None, (api_error("FORBIDDEN", "Insufficient permissions", 403))
    tenant = current_tenant()
    conn = apps.get(connection_id, tenant.id) if tenant is not None else None
    if conn is None or conn.provider != "discord" or conn.status != "active":
        return None, api_error("NOT_FOUND", "No such Discord connection", 404)
    return (member, conn), None


def _server_call(connection_id: int, fn, status: int = 200):
    from hubra.core.errors import api_error

    from .audit import AuditError
    from .dm import DmError
    from .layout import LayoutError
    from .server import ServerError

    found, error = _club_connection(connection_id)
    if error is not None:
        return error
    member, conn = found
    if request.method != "GET":
        # Support staff looking in as a member don't change the club's server.
        from hubra.api.permissions import refuse_during_impersonation

        refuse_during_impersonation()
    try:
        result = fn(member, conn)
    except (ServerError, AuditError, LayoutError, DmError) as exc:
        db.session.rollback()
        return api_error("DISCORD_FAILED", str(exc), 409)
    db.session.commit()
    return jsonify(result if result is not None else {"ok": True}), status


@discord_bp.get("/connections/<int:connection_id>/channels")
def server_channels(connection_id: int):
    from .server import overview

    return _server_call(connection_id, lambda m, c: overview(c))


@discord_bp.post("/connections/<int:connection_id>/channels")
def server_create_channel(connection_id: int):
    from .server import create

    body = request.get_json(silent=True) or {}
    roles = body.get("roleIds") or []
    return _server_call(
        connection_id,
        lambda m, c: create(
            c,
            str(body.get("name") or ""),
            str(body.get("kind") or "text"),
            parent_id=body.get("parentId") or None,
            topic=str(body.get("topic") or ""),
            private=bool(body.get("private")),
            role_ids=[str(r) for r in roles] if isinstance(roles, list) else [],
            who=m.name,
        ),
        201,
    )


@discord_bp.patch("/connections/<int:connection_id>/channels/<string:channel_id>")
def server_edit_channel(connection_id: int, channel_id: str):
    from .server import edit

    body = request.get_json(silent=True) or {}
    changes = {k: body[k] for k in ("name", "topic", "parentId") if k in body}
    return _server_call(
        connection_id, lambda m, c: edit(c, channel_id, changes, m.name)
    )


@discord_bp.delete("/connections/<int:connection_id>/channels/<string:channel_id>")
def server_delete_channel(connection_id: int, channel_id: str):
    from .server import delete

    return _server_call(connection_id, lambda m, c: delete(c, channel_id, m.name))


@discord_bp.get("/connections/<int:connection_id>/messages")
def server_messages(connection_id: int):
    from .server import messages

    return _server_call(connection_id, lambda m, c: {"messages": messages(c)})


@discord_bp.post("/connections/<int:connection_id>/messages/<string:key>/refresh")
def server_refresh_message(connection_id: int, key: str):
    from .server import refresh

    return _server_call(connection_id, lambda m, c: {"edited": refresh(c, key)})


@discord_bp.delete("/connections/<int:connection_id>/messages/<string:key>")
def server_stop_message(connection_id: int, key: str):
    from .server import stop

    return _server_call(connection_id, lambda m, c: stop(c, key))


# ── Channel layout, permission audit, direct messages ────────────────────────


def _audit_entry(member, conn, action: str, label: str) -> None:
    """Approvals and fixes of who sees a channel go to the club's audit log."""
    from hubra.api.utils import log_audit

    log_audit(member.id, action, "connected_app", conn.id, label[:255])


@discord_bp.get("/connections/<int:connection_id>/layout")
def server_layout(connection_id: int):
    from .layout import current

    return _server_call(connection_id, lambda m, c: current(c))


@discord_bp.post("/connections/<int:connection_id>/layout")
def server_apply_layout(connection_id: int):
    from .layout import apply

    body = request.get_json(silent=True) or {}
    slots = body.get("slots") if isinstance(body.get("slots"), list) else []

    def run(member, conn):
        result = apply(
            conn,
            [str(s) for s in slots],
            category_id=str(body.get("categoryId") or "") or None,
            category_name=str(body.get("categoryName") or ""),
            who=member.name,
        )
        if result["made"]:
            _audit_entry(
                member,
                conn,
                "discord.layout_created",
                "Discord: " + ", ".join(f"#{m['name']}" for m in result["made"]),
            )
        return result

    return _server_call(connection_id, run, 201)


@discord_bp.get("/connections/<int:connection_id>/audit")
def server_audit(connection_id: int):
    from .audit import run

    return _server_call(connection_id, lambda m, c: run(c))


@discord_bp.post("/connections/<int:connection_id>/audit/<string:channel_id>")
def server_audit_action(connection_id: int, channel_id: str):
    from . import audit

    action = str((request.get_json(silent=True) or {}).get("action") or "")
    actions = {
        "approve": (audit.approve, "discord.channel_public_approved"),
        "revoke": (audit.revoke, "discord.channel_public_revoked"),
        "members_only": (audit.members_only, "discord.channel_members_only"),
    }
    if action not in actions:
        from hubra.core.errors import api_error

        return api_error("VALIDATION_ERROR", "Unknown action", 400)
    fn, audit_action = actions[action]

    def run(member, conn):
        fn(conn, channel_id, member.name)
        _audit_entry(member, conn, audit_action, f"Discord-Kanal {channel_id}")
        return audit.run(conn)

    return _server_call(connection_id, run)


@discord_bp.get("/connections/<int:connection_id>/dm")
def server_dm_preview(connection_id: int):
    from .dm import preview

    source = str(request.args.get("source") or "")
    return _server_call(connection_id, lambda m, c: preview(c, source))


@discord_bp.post("/connections/<int:connection_id>/dm")
def server_dm_send(connection_id: int):
    from .dm import start

    body = request.get_json(silent=True) or {}

    def run(member, conn):
        result = start(
            conn,
            str(body.get("source") or ""),
            str(body.get("title") or ""),
            str(body.get("text") or ""),
            member.name,
        )
        _audit_entry(
            member,
            conn,
            "discord.dm_sent",
            f"Discord-Direktnachricht «{str(body.get('title') or '')[:80]}» an "
            f"{result['queued']}",
        )
        return result

    return _server_call(connection_id, run, 202)
