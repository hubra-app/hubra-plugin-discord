"""The Hubra Discord bot: connecting a server, announcements, role sync,
and slash commands and buttons through the signed interactions endpoint."""

from __future__ import annotations

import json
import time
from datetime import date, timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from hubra.core import apps
from hubra.core.models.connected_app import (
    ConnectedApp,
    ConnectedAppEvent,
    ExternalAccount,
)
from hubra.core.models.tenant import Tenant
from hubra_discord import api
from hubra_discord.api import DiscordError
from hubra_discord.app import handler
from hubra_discord.messages import payload
from tests.helpers import set_modules

H = {"X-Tenant-Slug": "club-a"}
GUILD = "900"
CHANNEL = "901"
ROLE_TRAINER = "910"
ROLE_MEMBER = "911"
ROLE_OTHER = "912"
SIGNING_KEY = Ed25519PrivateKey.generate()
APP_ID = "1111"


@pytest.fixture(autouse=True)
def _fresh_platform_key():
    """Each test makes its own platform key. Hubra's own fixture that
    forgets the last one may not reach plugin tests (older Hubra)."""
    from hubra.core import crypto

    forget = getattr(crypto, "forget_keys", None)
    if forget:
        forget()
    yield


@pytest.fixture
def discord_settings(app, db):
    from hubra.core.services import platform_settings

    public = SIGNING_KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    keys = {
        "discord.client_id": APP_ID,
        "discord.client_secret": "secret",
        "discord.bot_token": "bot-token",
        "discord.public_key": public,
    }
    platform_settings.set_many(keys)
    db.session.commit()
    yield
    db.session.rollback()
    platform_settings.set_many(dict.fromkeys(keys))
    db.session.commit()


@pytest.fixture
def tenant(db, seeded_db, discord_settings):
    t = Tenant(name="Club A", slug="club-a", status="active")
    db.session.add(t)
    db.session.commit()
    set_modules(True, "discord")
    return t


@pytest.fixture
def club_auth(client, tenant):
    client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.ch", "password": "password123"},
        headers=H,
    )
    return {}


def _conn(db, tenant, scopes=("announce", "roles", "commands"), **config):
    conn = ConnectedApp(
        tenant_id=tenant.id,
        provider="discord",
        external_id=GUILD,
        label="Club server",
        status="active",
        scopes=list(scopes),
        config={
            "channel_id": CHANNEL,
            "announce_events": True,
            "announce_posts": True,
            "announce_polls": True,
            "members_only_events": False,
            "mention": "",
            "role_map": [],
            **config,
        },
    )
    db.session.add(conn)
    db.session.commit()
    return conn


# ── The manifest ─────────────────────────────────────────────────────────────


def test_manifest_declares_the_app_tasks_and_schedule(app):
    from hubra.core.modules import registry

    module, provider = registry.app_provider("discord")
    assert module.code == "discord"
    assert [s.key for s in provider.scopes] == [
        "announce",
        "roles",
        "commands",
        "events",
        "channels",
        "notify",
    ]
    assert registry.processes()["discord-gateway"] == "hubra_discord.gateway:run"
    assert "hubra_discord.tasks" in registry.task_modules()
    assert any(
        s.task == "hubra_discord.tasks.sync_all_clubs" for s in registry.schedules()
    )


def test_unavailable_without_platform_settings(app):
    with app.app_context():
        assert handler.unavailable() is not None


# ── Connecting a server ──────────────────────────────────────────────────────


def test_authorize_url_asks_for_little(app, discord_settings):
    with app.app_context():
        url = urlparse(
            handler.authorize_url("st", "https://api/cb", ["announce", "roles"])
        )
    query = parse_qs(url.query)
    assert query["scope"] == ["bot applications.commands"]
    bits = int(query["permissions"][0])
    assert bits == (
        api.VIEW_CHANNEL
        | api.SEND_MESSAGES
        | api.EMBED_LINKS
        | api.MANAGE_ROLES
        | api.CHANGE_NICKNAME
    )
    assert not bits & 8  # never Administrator
    assert query["state"] == ["st"]


def test_server_comes_from_discord_not_from_the_url(app, discord_settings):
    calls = []

    def fake_form(path, fields):
        calls.append(path)
        if path == "/oauth2/token":
            return {"access_token": "t", "guild": {"id": GUILD, "name": "Real server"}}
        return {}

    with app.app_context(), patch.object(api, "form", side_effect=fake_form):
        draft = handler.complete({"code": "c", "guild_id": "666"}, "https://api/cb")
    assert draft.external_id == GUILD
    assert draft.label == "Real server"
    assert calls == ["/oauth2/token", "/oauth2/token/revoke"]


def test_no_server_in_the_answer_is_refused(app, discord_settings):
    with (
        app.app_context(),
        patch.object(api, "form", return_value={"access_token": "t"}),
    ):
        with pytest.raises(apps.AppError):
            handler.complete({"code": "c"}, "https://api/cb")


def test_connect_confirm_and_disconnect(client, db, club_auth, tenant):
    resp = client.post("/api/v1/apps/discord/connect", json={}, headers=H)
    assert resp.status_code == 200, resp.get_json()
    state = parse_qs(urlparse(resp.get_json()["url"]).query)["state"][0]
    token = {"access_token": "t", "guild": {"id": GUILD, "name": "Club server"}}
    with patch.object(api, "form", return_value=token):
        resp = client.get(
            f"/api/v1/apps/callback/discord?state={state}&code=c&guild_id={GUILD}"
        )
    conn_id = int(parse_qs(urlparse(resp.headers["Location"]).query)["confirm"][0])

    with (
        patch("hubra_discord.tasks.sync_connection.delay") as sync,
        patch("hubra_discord.tasks.register_commands.delay") as register,
        patch("hubra_discord.tasks.apply_profile.delay") as profile,
    ):
        resp = client.post(f"/api/v1/apps/connections/{conn_id}/confirm", headers=H)
    assert resp.status_code == 200
    sync.assert_called_once_with(conn_id)
    register.assert_called_once()
    profile.assert_called_once_with(conn_id)
    assert apps.active("discord", GUILD).id == conn_id

    with patch.object(api, "bot") as bot:
        assert (
            client.delete(f"/api/v1/apps/connections/{conn_id}", headers=H).status_code
            == 200
        )
    bot.assert_called_once_with("DELETE", f"/users/@me/guilds/{GUILD}")
    assert apps.active("discord", GUILD) is None


# ── Settings ─────────────────────────────────────────────────────────────────


def _server(method, path, body=None, **_):
    if path.endswith("/channels"):
        return [
            {"id": CHANNEL, "name": "news", "type": 0},
            {"id": "902", "name": "Voice", "type": 2},
        ]
    if path.endswith("/roles"):
        return [
            {"id": GUILD, "name": "@everyone", "position": 0},
            {"id": ROLE_TRAINER, "name": "Trainer", "position": 1},
            {"id": ROLE_MEMBER, "name": "Mitglied", "position": 2},
            {"id": "950", "name": "Hubra", "position": 5, "managed": True},
            {"id": "960", "name": "Admin", "position": 9},
        ]
    if path.endswith(f"/members/{APP_ID}"):
        return {"roles": ["950"]}
    if path == f"/channels/{CHANNEL}":
        return {"id": CHANNEL, "name": "news", "type": 0, "permission_overwrites": []}
    raise AssertionError(path)


def test_options_list_text_channels_and_assignable_roles(client, db, club_auth, tenant):
    conn = _conn(db, tenant)
    with patch.object(api, "bot", side_effect=_server):
        data = client.get(
            f"/api/v1/apps/connections/{conn.id}/options", headers=H
        ).get_json()
    assert data["channels"] == [{"id": CHANNEL, "name": "news"}]
    roles = {r["name"]: r["assignable"] for r in data["roles"]}
    assert roles == {"Admin": False, "Mitglied": True, "Trainer": True}
    assert {"key": "members", "label": "Active members", "kind": "members"} in data[
        "sources"
    ]


def test_settings_are_checked_against_the_server(
    client, db, club_auth, tenant, seeded_db
):
    conn = _conn(db, tenant, channel_id=None)
    url = f"/api/v1/apps/connections/{conn.id}"
    with patch.object(api, "bot", side_effect=_server):
        assert (
            client.patch(
                url, json={"config": {"channel_id": "902"}}, headers=H
            ).status_code
            == 400
        )
        bad_role = {"role_map": [{"source": "members", "role_id": "960"}]}
        assert (
            client.patch(url, json={"config": bad_role}, headers=H).status_code == 400
        )
        assert (
            client.patch(
                url, json={"config": {"mention": "role:404"}}, headers=H
            ).status_code
            == 400
        )
        good = {
            "channel_id": CHANNEL,
            "mention": f"role:{ROLE_MEMBER}",
            "role_map": [{"source": "members", "role_id": ROLE_MEMBER}],
        }
        with patch("hubra_discord.tasks.sync_connection.delay") as sync:
            resp = client.patch(url, json={"config": good}, headers=H)
    assert resp.status_code == 200, resp.get_json()
    config = resp.get_json()["config"]
    assert config["channel_id"] == CHANNEL
    assert config["role_map"] == [{"source": "members", "role_id": ROLE_MEMBER}]
    sync.assert_called_once_with(conn.id)  # new mapping: sync right away


def test_channel_per_kind_is_checked_against_the_server(client, db, club_auth, tenant):
    conn = _conn(db, tenant)
    url = f"/api/v1/apps/connections/{conn.id}"
    with patch.object(api, "bot", side_effect=_server):
        bad = client.patch(
            url, json={"config": {"channels": {"reminders": "999"}}}, headers=H
        )
        unknown = client.patch(
            url, json={"config": {"channels": {"gossip": CHANNEL}}}, headers=H
        )
        good = client.patch(
            url,
            json={"config": {"channels": {"reminders": CHANNEL, "posts": None}}},
            headers=H,
        )
    assert bad.status_code == 400 and unknown.status_code == 400
    assert good.status_code == 200, good.get_json()
    assert good.get_json()["config"]["channels"] == {"reminders": CHANNEL}


# ── Announcements ────────────────────────────────────────────────────────────


@pytest.fixture
def queued():
    with patch("hubra_discord.tasks.send_announcement.delay") as delay:
        yield delay


def test_public_event_is_announced_with_buttons(client, db, club_auth, tenant, queued):
    conn = _conn(db, tenant, mention="everyone")
    resp = client.post(
        "/api/v1/events/",
        json={
            "title": "Summer party",
            "date": "2026-07-04",
            "type": "Social",
            "location": "Garden",
            "is_public": True,
        },
        headers=H,
    )
    assert resp.status_code == 201, resp.get_json()
    connection_id, body, kind = queued.call_args.args[:3]
    assert kind == "events"
    assert connection_id == conn.id  # an id in the queue, never a token
    assert body["content"] == "@everyone"
    assert "url" not in body["embeds"][0] or "/club-a/app/" in body["embeds"][0]["url"]
    custom_ids = [b["custom_id"] for b in body["components"][0]["components"]]
    assert custom_ids[0].startswith("register:")


def test_members_only_event_stays_off_discord(client, db, club_auth, tenant, queued):
    _conn(db, tenant)
    client.post(
        "/api/v1/events/",
        json={"title": "AGM", "date": "2026-07-04", "type": "Meeting"},
        headers=H,
    )
    queued.assert_not_called()


def test_members_only_event_when_the_club_says_so(
    client, db, club_auth, tenant, queued
):
    _conn(db, tenant, members_only_events=True)
    client.post(
        "/api/v1/events/",
        json={"title": "AGM", "date": "2026-07-04", "type": "Meeting"},
        headers=H,
    )
    queued.assert_called_once()


def test_post_goes_out_without_its_text(client, db, club_auth, tenant, queued):
    _conn(db, tenant)
    client.post(
        "/api/v1/posts/",
        json={"title": "News", "summary": "Short", "body": "The long private text"},
        headers=H,
    )
    item = queued.call_args.args[1]["embeds"][0]
    assert item["description"] == "Short"
    assert "private" not in json.dumps(item)


def test_poll_for_some_groups_only_stays_off_discord(
    client, db, club_auth, tenant, queued
):
    from hubra.core.models.group import Group

    _conn(db, tenant)
    group = Group(name="Board")
    db.session.add(group)
    db.session.commit()
    client.post(
        "/api/v1/polls/",
        json={
            "question": "Budget?",
            "options": [{"label": "Yes"}, {"label": "No"}],
            "required_group_ids": [group.id],
        },
        headers=H,
    )
    queued.assert_not_called()
    client.post(
        "/api/v1/polls/",
        json={"question": "Pizza?", "options": [{"label": "Yes"}, {"label": "No"}]},
        headers=H,
    )
    assert "1️⃣ Yes" in queued.call_args.args[1]["embeds"][0]["description"]


def test_nothing_without_the_announce_scope(client, db, club_auth, tenant, queued):
    _conn(db, tenant, scopes=("roles",))
    client.post(
        "/api/v1/events/",
        json={"title": "x", "date": "2026-07-04", "type": "Social", "is_public": True},
        headers=H,
    )
    queued.assert_not_called()


def test_nothing_when_the_module_is_off(client, db, club_auth, tenant, queued):
    _conn(db, tenant)
    set_modules(False, "discord")
    client.post(
        "/api/v1/events/",
        json={"title": "x", "date": "2026-07-04", "type": "Social", "is_public": True},
        headers=H,
    )
    queued.assert_not_called()


def test_only_the_configured_mention_pings():
    assert payload("", [])["allowed_mentions"] == {"parse": []}
    assert payload("here", [])["content"] == "@here"
    role = payload("role:5", [])
    assert role["content"] == "<@&5>" and role["allowed_mentions"] == {
        "parse": [],
        "roles": ["5"],
    }


def test_announcement_task_posts_and_records(app, db, tenant):
    from hubra_discord.tasks import send_announcement

    conn = _conn(db, tenant, club_embeds=False)
    body = payload("", [{"title": "Hi"}])
    with patch.object(api, "bot") as bot:
        send_announcement.run(conn.id, body)
    bot.assert_called_once_with("POST", f"/channels/{CHANNEL}/messages", body)
    event = db.session.query(ConnectedAppEvent).filter_by(action="announced").one()
    assert event.ok and event.detail == f"Hi · channel {CHANNEL}"


def test_each_kind_goes_to_its_own_channel_or_the_main_one(app, db, tenant):
    from hubra_discord.tasks import send_announcement

    conn = _conn(db, tenant, channels={"reminders": "905"})
    body = payload("", [{"title": "Hi"}])
    with patch.object(api, "bot") as bot:
        send_announcement.run(conn.id, body, "reminders")
        send_announcement.run(conn.id, body, "posts")
    assert [c.args[1] for c in bot.call_args_list] == [
        "/channels/905/messages",
        f"/channels/{CHANNEL}/messages",
    ]


def test_announcement_task_records_a_lost_permission(app, db, tenant):
    from hubra_discord.tasks import send_announcement

    conn = _conn(db, tenant)
    with patch.object(api, "bot", side_effect=DiscordError("no", 403)):
        send_announcement.run(conn.id, payload("", [{"title": "Hi"}]))
    db.session.refresh(conn)
    assert "permission" in conn.last_error


SEND_BITS = api.VIEW_CHANNEL | api.SEND_MESSAGES | api.EMBED_LINKS


def _locked_server(method, path, body=None, **_):
    """The Hubra role may post on the server, but #news denies it."""
    if path == f"/guilds/{GUILD}/channels":
        return [{"id": CHANNEL, "name": "news", "type": 0}]
    if path == f"/channels/{CHANNEL}/messages":
        raise DiscordError("Discord answered 403 (code 50013)", 403, code=50013)
    if path == f"/channels/{CHANNEL}":
        return {
            "id": CHANNEL,
            "name": "news",
            "type": 0,
            "permission_overwrites": [
                {"id": GUILD, "type": 0, "allow": "0", "deny": str(api.SEND_MESSAGES)},
            ],
        }
    if path.endswith("/roles"):
        return [
            {"id": GUILD, "name": "@everyone", "position": 0, "permissions": "0"},
            {
                "id": "950",
                "name": "Hubra",
                "position": 5,
                "permissions": str(SEND_BITS),
            },
        ]
    if path.endswith(f"/members/{APP_ID}"):
        return {"roles": ["950"]}
    if path.endswith("/messages"):
        return {"id": "777"}
    raise AssertionError(path)


def test_a_refused_post_names_the_channel_and_the_permission(app, db, tenant):
    from hubra_discord.tasks import send_announcement

    conn = _conn(db, tenant)
    with patch.object(api, "bot", side_effect=_locked_server):
        send_announcement.run(conn.id, payload("", [{"title": "Hi"}]), "events")
    db.session.refresh(conn)
    err = conn.last_error
    assert '"Hi"' in err and "#news" in err
    assert "Send Messages" in err and "View Channels" not in err
    assert "50013 Missing Permissions" in err


def test_channel_permissions_follow_discord_rules(app, discord_settings):
    from hubra_discord.diagnose import ALL, channel_permissions

    channel = {
        "permission_overwrites": [
            {"id": GUILD, "type": 0, "allow": "0", "deny": str(api.SEND_MESSAGES)},
            {"id": "950", "type": 0, "allow": str(api.SEND_MESSAGES), "deny": "0"},
        ]
    }
    with patch.object(api, "bot", side_effect=_locked_server):
        assert channel_permissions(GUILD, channel) & api.SEND_MESSAGES
        assert (
            channel_permissions(
                GUILD, {"permission_overwrites": channel["permission_overwrites"][:1]}
            )
            & api.SEND_MESSAGES
            == 0
        )

    def admin(method, path, body=None, **_):
        if path.endswith("/roles"):
            return [{"id": "950", "permissions": str(api.ADMINISTRATOR)}]
        return {"roles": ["950"]}

    with patch.object(api, "bot", side_effect=admin):
        assert channel_permissions(GUILD, channel) == ALL


def test_options_list_channel_problems(client, db, club_auth, tenant):
    conn = _conn(db, tenant, log_channel_id=CHANNEL)
    with patch.object(api, "bot", side_effect=_locked_server):
        data = client.get(
            f"/api/v1/apps/connections/{conn.id}/options", headers=H
        ).get_json()
    assert data["channelProblems"] == [
        {"channel": "#news", "uses": ["main", "log"], "missing": ["Send Messages"]}
    ]


def test_a_skipped_members_only_event_is_logged(client, db, club_auth, tenant, queued):
    conn = _conn(db, tenant)
    client.post(
        "/api/v1/events/",
        json={"title": "AGM", "date": "2026-07-04", "type": "Social"},
        headers=H,
    )
    queued.assert_not_called()
    event = (
        db.session.query(ConnectedAppEvent)
        .filter_by(connection_id=conn.id, action="skipped")
        .one()
    )
    assert 'Event "AGM"' in event.detail and "nur für Mitglieder" in event.detail


def test_a_switched_off_kind_is_logged(client, db, club_auth, tenant, queued):
    conn = _conn(db, tenant, announce_polls=False)
    client.post(
        "/api/v1/polls/",
        json={"question": "Pizza?", "options": [{"label": "Yes"}, {"label": "No"}]},
        headers=H,
    )
    queued.assert_not_called()
    event = (
        db.session.query(ConnectedAppEvent)
        .filter_by(connection_id=conn.id, action="skipped")
        .one()
    )
    assert "Neue Umfragen posten" in event.detail


def test_errors_go_to_the_log_channel(app, db, tenant):
    from hubra_discord.tasks import post_log, send_announcement

    conn = _conn(db, tenant, log_channel_id="903")
    with (
        patch.object(api, "bot", side_effect=_locked_server),
        patch.object(post_log, "delay") as delay,
    ):
        send_announcement.run(conn.id, payload("", [{"title": "Hi"}]))
    action, ok, detail = delay.call_args.args[1:]
    assert action == "announced" and ok is False and "#news" in detail

    with patch.object(api, "bot", side_effect=_locked_server) as bot:
        post_log.run(conn.id, action, ok, detail)
    method, path, body = bot.call_args.args
    assert path == "/channels/903/messages"
    assert body["embeds"][0]["title"].startswith("❌")


def test_successes_reach_the_log_channel_only_when_asked(app, db, tenant):
    from hubra_discord.tasks import post_log, send_announcement

    conn = _conn(db, tenant, log_channel_id="903")
    with patch.object(api, "bot"), patch.object(post_log, "delay") as delay:
        send_announcement.run(conn.id, payload("", [{"title": "Hi"}]))
    delay.assert_not_called()
    conn.config = {**conn.config, "log_level": "all"}
    db.session.commit()
    with patch.object(api, "bot"), patch.object(post_log, "delay") as delay:
        send_announcement.run(conn.id, payload("", [{"title": "Hi"}]))
    assert delay.call_args.args[2] is True


def test_the_test_checks_every_channel(client, db, club_auth, tenant):
    conn = _conn(db, tenant, channels={"events": CHANNEL}, log_channel_id="903")
    with patch.object(api, "bot", side_effect=_locked_server):
        resp = client.post(f"/api/v1/apps/connections/{conn.id}/test", headers=H)
    assert resp.status_code == 502
    assert "#news" in str(resp.get_json())


def test_disconnected_connection_sends_nothing(app, db, tenant):
    from hubra_discord.tasks import send_announcement

    conn = _conn(db, tenant)
    conn.status = "revoked"
    db.session.commit()
    with patch.object(api, "bot") as bot:
        send_announcement.run(conn.id, payload("", [{"title": "Hi"}]))
    bot.assert_not_called()


# ── Role sync ────────────────────────────────────────────────────────────────


@pytest.fixture
def trainer(db, tenant, seeded_db):
    from hubra.core.models.group import Group

    admin = seeded_db["admin"]
    admin.status = "active"
    group = Group(name="Trainer", members=[admin])
    db.session.add(group)
    db.session.add(
        ExternalAccount(
            user_id=seeded_db["user"].id, provider="discord", external_id="42"
        )
    )
    db.session.commit()
    return group


class FakeServer:
    def __init__(self, roles=(), on_server=True):
        self.roles = set(roles)
        self.on_server = on_server
        self.calls = []

    def __call__(self, method, path, body=None, **kw):
        self.calls.append((method, path))
        if path.endswith("/roles") and method == "GET":
            return [
                {"id": ROLE_TRAINER, "name": "Trainer"},
                {"id": ROLE_MEMBER, "name": "Mitglied"},
            ]
        if path.endswith("/members/42") and method == "GET":
            if not self.on_server:
                raise DiscordError("unknown member", 404)
            return {"roles": sorted(self.roles)}
        if "/roles/" in path:
            role = path.rsplit("/", 1)[1]
            (self.roles.add if method == "PUT" else self.roles.discard)(role)
            return None
        raise AssertionError(path)


def _mapped(db, tenant, group):
    return _conn(
        db,
        tenant,
        role_map=[
            {"source": f"group:{group.id}", "role_id": ROLE_TRAINER},
            {"source": "members", "role_id": ROLE_MEMBER},
        ],
    )


def test_sync_gives_mapped_roles_and_leaves_others(app, db, tenant, trainer):
    from hubra_discord.tasks import sync_connection

    conn = _mapped(db, tenant, trainer)
    server = FakeServer(roles={ROLE_OTHER})
    with patch.object(api, "bot", side_effect=server):
        assert sync_connection.run(conn.id) == 1
    assert server.roles == {ROLE_TRAINER, ROLE_MEMBER, ROLE_OTHER}
    detail = db.session.query(ConnectedAppEvent).filter_by(action="roles").one().detail
    assert "+Trainer" in detail and "+Mitglied" in detail


def test_leaving_the_group_takes_the_role(
    client, db, club_auth, tenant, trainer, seeded_db
):
    from hubra_discord.tasks import sync_member

    conn = _mapped(db, tenant, trainer)
    with patch("hubra_discord.tasks.sync_member.delay") as delay:
        resp = client.delete(
            f"/api/v1/groups/{trainer.id}/members/{seeded_db['admin'].id}", headers=H
        )
    assert resp.status_code in (200, 204), resp.get_json()
    delay.assert_called_once_with(conn.id, seeded_db["admin"].id)
    server = FakeServer(roles={ROLE_TRAINER, ROLE_MEMBER})
    with patch.object(api, "bot", side_effect=server):
        sync_member.run(conn.id, seeded_db["admin"].id)
    assert server.roles == {ROLE_MEMBER}


def test_suspended_member_loses_every_mapped_role(app, db, tenant, trainer, seeded_db):
    from hubra_discord.tasks import sync_member

    conn = _mapped(db, tenant, trainer)
    seeded_db["admin"].status = "suspended"
    db.session.commit()
    server = FakeServer(roles={ROLE_TRAINER, ROLE_MEMBER, ROLE_OTHER})
    with patch.object(api, "bot", side_effect=server):
        sync_member.run(conn.id, seeded_db["admin"].id)
    assert server.roles == {ROLE_OTHER}


def test_member_not_on_the_server_is_skipped(app, db, tenant, trainer, seeded_db):
    from hubra_discord.tasks import sync_member

    conn = _mapped(db, tenant, trainer)
    server = FakeServer(on_server=False)
    with patch.object(api, "bot", side_effect=server):
        assert sync_member.run(conn.id, seeded_db["admin"].id) is None
    assert not any(m == "PUT" for m, _ in server.calls)


def test_unlinking_removes_the_mapped_roles(app, db, tenant, trainer):
    from hubra_discord.tasks import remove_roles

    conn = _mapped(db, tenant, trainer)
    server = FakeServer(roles={ROLE_TRAINER, ROLE_OTHER})
    with patch.object(api, "bot", side_effect=server):
        remove_roles.run(conn.id, "42")
    assert server.roles == {ROLE_OTHER}


def test_unlink_event_queues_the_removal(app, db, tenant, trainer, seeded_db):
    from hubra.core.modules import emit
    from hubra.core.tenant.context import in_club

    conn = _mapped(db, tenant, trainer)
    with (
        patch("hubra_discord.tasks.remove_roles.delay") as delay,
        app.test_request_context(),
    ):
        with in_club(tenant):
            emit(
                "account.unlinked",
                member_id=seeded_db["admin"].id,
                provider="discord",
                external_id="42",
            )
            db.session.commit()
    delay.assert_called_once_with(conn.id, "42")


def test_no_role_changes_without_the_roles_scope(app, db, tenant, trainer, seeded_db):
    from hubra_discord.tasks import sync_member

    conn = _mapped(db, tenant, trainer)
    conn.scopes = ["announce"]
    db.session.commit()
    with patch.object(api, "bot") as bot:
        sync_member.run(conn.id, seeded_db["admin"].id)
    bot.assert_not_called()


# ── Interactions (slash commands, buttons) ───────────────────────────────────


def _signed(client, body: dict, timestamp=None, key=SIGNING_KEY):
    raw = json.dumps(body).encode()
    ts = str(timestamp or int(time.time()))
    signature = key.sign(ts.encode() + raw).hex()
    return client.post(
        "/api/v1/discord/interactions",
        data=raw,
        content_type="application/json",
        headers={"X-Signature-Ed25519": signature, "X-Signature-Timestamp": ts},
    )


def test_unsigned_or_forged_requests_are_refused(client, tenant):
    assert (
        client.post("/api/v1/discord/interactions", json={"type": 1}).status_code == 401
    )
    assert (
        _signed(client, {"type": 1}, key=Ed25519PrivateKey.generate()).status_code
        == 401
    )
    assert (
        _signed(client, {"type": 1}, timestamp=int(time.time()) - 3600).status_code
        == 401
    )


def test_ping(client, tenant):
    resp = _signed(client, {"type": 1})
    assert resp.status_code == 200 and resp.get_json() == {"type": 1}


def _command(name, user="42", guild=GUILD):
    return {
        "type": 2,
        "guild_id": guild,
        "member": {"user": {"id": user}},
        "data": {"name": name},
    }


def test_unconnected_server(client, tenant):
    data = _signed(client, _command("events", guild="777")).get_json()
    assert "keinem Verein" in data["data"]["content"]
    assert data["data"]["flags"] == 64


def test_events_command_shows_public_events_to_strangers(client, db, tenant):
    from hubra.core.models.event import ClubEvent

    _conn(db, tenant)
    soon = date.today() + timedelta(days=3)
    db.session.add_all(
        [
            ClubEvent(title="Open day", date=soon, type="Social", is_public=True),
            ClubEvent(
                title="Board meeting", date=soon, type="Meeting", is_public=False
            ),
        ]
    )
    db.session.commit()
    text = _signed(client, _command("events", user="999")).get_json()["data"]["content"]
    assert "Open day" in text and "Board meeting" not in text
    assert "Verknüpfe" in text


def test_events_command_shows_members_their_events(client, db, tenant, trainer):
    from hubra.core.models.event import ClubEvent

    _conn(db, tenant)
    db.session.add(
        ClubEvent(
            title="Board meeting", date=date.today() + timedelta(days=1), type="Meeting"
        )
    )
    db.session.commit()
    text = _signed(client, _command("events")).get_json()["data"]["content"]
    assert "Board meeting" in text


def test_button_signs_a_linked_member_up(client, db, tenant, trainer, seeded_db):
    from hubra.core.models.event import ClubEvent, EventRegistration

    _conn(db, tenant)
    event = ClubEvent(
        title="Training",
        date=date.today() + timedelta(days=2),
        type="Training",
        is_public=True,
    )
    db.session.add(event)
    db.session.commit()
    click = {
        "type": 3,
        "guild_id": GUILD,
        "member": {"user": {"id": "42"}},
        "data": {"custom_id": f"register:{event.id}"},
    }
    text = _signed(client, click).get_json()["data"]["content"]
    assert "Angemeldet" in text
    reg = db.session.query(EventRegistration).filter_by(event_id=event.id).one()
    assert reg.member_id == seeded_db["admin"].id
    again = _signed(client, click).get_json()["data"]["content"]
    assert "bereits" in again
    click["data"]["custom_id"] = f"unregister:{event.id}"
    assert "Abgemeldet" in _signed(client, click).get_json()["data"]["content"]


def test_button_from_someone_not_linked(client, db, tenant):
    _conn(db, tenant)
    click = {
        "type": 3,
        "guild_id": GUILD,
        "member": {"user": {"id": "999"}},
        "data": {"custom_id": "register:1"},
    }
    assert "Verknüpfe" in _signed(client, click).get_json()["data"]["content"]


def test_buttons_off_without_the_commands_scope(client, db, tenant, trainer):
    _conn(db, tenant, scopes=("announce",))
    click = {
        "type": 3,
        "guild_id": GUILD,
        "member": {"user": {"id": "42"}},
        "data": {"custom_id": "register:1"},
    }
    assert "ausgeschaltet" in _signed(client, click).get_json()["data"]["content"]


def test_status_command_for_admins(client, db, tenant, trainer):
    _conn(db, tenant)
    text = _signed(client, _command("hubra")).get_json()["data"]["content"]
    assert "Club server" in text and "verknüpftem Discord: 1" in text
    text = _signed(client, _command("hubra", user="999")).get_json()["data"]["content"]
    assert "Nur wer" in text


# ── Reminders (from the club's reminder job) ─────────────────────────────────


def _remind(db, tenant, hook, **payload):
    from hubra.core.tenant.context import in_club

    from hubra_discord import hooks

    with in_club(tenant):
        getattr(hooks, hook)(tenant_id=tenant.id, **payload)
        db.session.commit()


def test_event_reminder_goes_to_the_reminders_channel(
    client, db, club_auth, tenant, queued
):
    conn = _conn(db, tenant, announce_events=False, announce_reminders=True)
    resp = client.post(
        "/api/v1/events/",
        json={
            "title": "Training",
            "date": "2026-07-04",
            "type": "Social",
            "is_public": True,
        },
        headers=H,
    )
    queued.assert_not_called()  # new events are switched off
    _remind(db, tenant, "on_event_reminder", event_id=resp.get_json()["id"])
    connection_id, body, kind = queued.call_args.args[:3]
    assert (connection_id, kind) == (conn.id, "reminders")
    assert body["embeds"][0]["title"].startswith("⏰")
    assert body["components"]  # sign-up buttons


def test_reminders_are_off_unless_the_club_turns_them_on(
    client, db, club_auth, tenant, queued
):
    _conn(db, tenant, announce_events=False)
    resp = client.post(
        "/api/v1/events/",
        json={
            "title": "T",
            "date": "2026-07-04",
            "type": "Social",
            "is_public": True,
        },
        headers=H,
    )
    _remind(db, tenant, "on_event_reminder", event_id=resp.get_json()["id"])
    queued.assert_not_called()


def test_members_only_event_reminder_stays_off_discord(
    client, db, club_auth, tenant, queued
):
    _conn(db, tenant, announce_events=False, announce_reminders=True)
    resp = client.post(
        "/api/v1/events/",
        json={"title": "AGM", "date": "2026-07-04", "type": "Meeting"},
        headers=H,
    )
    _remind(db, tenant, "on_event_reminder", event_id=resp.get_json()["id"])
    queued.assert_not_called()


def test_poll_closing_names_no_one(client, db, club_auth, tenant, queued):
    _conn(db, tenant, announce_polls=False, announce_reminders=True)
    resp = client.post(
        "/api/v1/polls/",
        json={"question": "Pizza?", "options": [{"label": "Yes"}, {"label": "No"}]},
        headers=H,
    )
    _remind(db, tenant, "on_poll_closing", poll_id=resp.get_json()["id"])
    _, body, kind = queued.call_args.args[:3]
    assert kind == "reminders"
    item = body["embeds"][0]
    assert item["title"].startswith("⏳") and "Pizza?" in item["title"]
    assert "Noch nicht abgestimmt" in item["description"]


# ── The club's look ──────────────────────────────────────────────────────────

PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _brand(db, tenant, **branding):
    tenant.settings = {**(tenant.settings or {}), "branding": branding}
    db.session.commit()


def test_announcements_carry_the_club_look(app, db, tenant):
    from hubra_discord.tasks import send_announcement

    app.config["API_PUBLIC_URL"] = "https://api.example.org"
    _brand(db, tenant, club_name="FC Example", primary_color="#12ab34", logo=PNG)
    conn = _conn(db, tenant)
    try:
        with patch.object(api, "bot") as bot:
            send_announcement.run(conn.id, payload("", [{"title": "Hi", "color": 1}]))
    finally:
        app.config["API_PUBLIC_URL"] = ""
    embed = bot.call_args.args[2]["embeds"][0]
    assert embed["color"] == 0x12AB34
    assert embed["author"]["name"] == "FC Example"
    assert embed["author"]["icon_url"].startswith(
        "https://api.example.org/api/v1/system/clubs/club-a/logo?v="
    )


def test_profile_sets_nick_avatar_and_bio_once(app, db, tenant):
    from hubra_discord.tasks import apply_profile

    _brand(db, tenant, club_name="FC Example", logo=PNG, locale="de-CH")
    conn = _conn(db, tenant)
    with patch.object(api, "bot") as bot:
        apply_profile.run(conn.id)
        apply_profile.run(conn.id)  # nothing changed: no second call
    path = f"/guilds/{GUILD}/members/@me"
    assert [c.args[:2] for c in bot.call_args_list] == [("PATCH", path)] * 2
    assert bot.call_args_list[0].args[2] == {"nick": "FC Example"}
    second = bot.call_args_list[1].args[2]
    assert second["avatar"] == PNG
    assert second["bio"] == "Der Hubra-Bot von FC Example."
    event = db.session.query(ConnectedAppEvent).filter_by(action="profile").one()
    assert event.ok

    _brand(db, tenant, club_name="FC Renamed", logo=PNG)
    with patch.object(api, "bot") as bot:
        apply_profile.run(conn.id)
    assert bot.call_args_list[0].args[2] == {"nick": "FC Renamed"}


def test_svg_logo_is_left_out_of_the_avatar(app, db, tenant):
    from hubra_discord.profile import desired

    svg = "data:image/svg+xml;base64,PHN2Zy8+"
    _brand(db, tenant, logo=svg)
    conn = _conn(db, tenant)
    assert desired(conn, tenant)["avatar"] is None
    assert desired(conn, tenant)["nick"] == "Club A"


def test_profile_off_resets_to_the_app(app, db, tenant):
    from hubra_discord.tasks import apply_profile

    conn = _conn(db, tenant, club_profile=False)
    with patch.object(api, "bot") as bot:
        apply_profile.run(conn.id)
    assert bot.call_args_list[0].args[2] == {"nick": None}
    assert bot.call_args_list[1].args[2] == {"avatar": None, "bio": None}


def test_missing_nickname_permission_still_sets_the_logo(app, db, tenant):
    from hubra_discord.tasks import apply_profile

    conn = _conn(db, tenant)
    calls = []

    def fake(method, path, body=None, **_):
        calls.append(body)
        if "nick" in body:
            raise DiscordError("Missing Permissions", 403)

    with patch.object(api, "bot", side_effect=fake):
        apply_profile.run(conn.id)
    assert len(calls) == 2  # the avatar and bio went on
    db.session.refresh(conn)
    assert "Change Nickname" in conn.last_error
    assert "profile_applied" not in (conn.config or {})  # tried again tomorrow


def test_saving_the_look_queues_the_profile(client, db, club_auth, tenant):
    conn = _conn(db, tenant)
    with patch("hubra_discord.tasks.apply_profile.delay") as queued:
        resp = client.patch(
            f"/api/v1/apps/connections/{conn.id}",
            json={"config": {"club_profile": False, "club_embeds": False}},
            headers=H,
        )
    assert resp.status_code == 200, resp.get_json()
    queued.assert_called_once_with(conn.id)
    db.session.refresh(conn)
    assert conn.config["club_profile"] is False
    assert conn.config["club_embeds"] is False


def test_nightly_run_refreshes_the_profile(app, db, tenant):
    from hubra_discord.tasks import sync_all_clubs

    conn = _conn(db, tenant)
    with (
        patch("hubra_discord.tasks.sync_connection.delay"),
        patch("hubra_discord.tasks.sync_events.delay") as events,
        patch("hubra_discord.tasks.apply_profile.delay") as profile,
        patch("hubra_discord.tasks.audit_connection.delay") as audit,
    ):
        sync_all_clubs.run()
    profile.assert_called_once_with(conn.id)
    events.assert_called_once_with(conn.id)
    audit.assert_called_once_with(conn.id)


# ── Direct messages (notifications) ──────────────────────────────────────────


class FakeDiscord:
    """Records calls; answers DM channels, messages, events, roles, channels."""

    def __init__(self, fail_dm=None):
        self.calls = []
        self.fail_dm = fail_dm
        self.next_id = 5000

    def _id(self):
        self.next_id += 1
        return str(self.next_id)

    def __call__(self, method, path, body=None, **kw):
        self.calls.append((method, path, body))
        if path == "/users/@me/channels":
            if self.fail_dm:
                raise DiscordError("Cannot send messages to this user", self.fail_dm)
            return {"id": "dm-1"}
        if path.endswith("/messages"):
            return {"id": self._id()}
        if path.endswith("/scheduled-events") and method == "POST":
            return {"id": self._id()}
        if "/scheduled-events/" in path:
            return {}
        if path.endswith("/roles") and method == "POST":
            return {"id": self._id()}
        if path.endswith("/channels") and method == "POST":
            return {"id": self._id()}
        if path.startswith("/channels/") and method == "GET":
            return {"id": path.rsplit("/", 1)[1]}
        return _server(method, path, body)

    def sent(self, path_end):
        return [b for m, p, b in self.calls if p.endswith(path_end) and m == "POST"]


def test_notification_goes_out_as_a_direct_message(app, db, tenant, trainer, seeded_db):
    from hubra.api.notifications.services import notify_member
    from hubra.core.tenant.context import in_club

    from hubra.core.tasks.connected_app_tasks import deliver_app_notification

    _conn(db, tenant, scopes=("notify",))
    admin = seeded_db["admin"]
    fake = FakeDiscord()
    queued = []
    with in_club(tenant):
        admin.notify_prefs = {"posts": {"discord": True}}
        db.session.commit()
        with (
            patch.object(api, "bot", side_effect=fake),
            # Run the task here after the commit (eager Celery would open
            # its own app).
            patch.object(
                deliver_app_notification,
                "delay",
                side_effect=lambda *a: queued.append(a),
            ),
        ):
            notify_member(
                admin.id, "info", "New post", "Hello club", link="/app/x", topic="posts"
            )
            db.session.commit()
            for args in queued:
                deliver_app_notification.run(*args)
    (dm,) = fake.sent("/users/@me/channels")
    assert dm == {"recipient_id": "42"}
    (msg,) = fake.sent("/channels/dm-1/messages")
    embed = msg["embeds"][0]
    assert embed["title"] and embed["description"] == "Hello club"
    assert embed["author"]["name"] == "Club A"  # the club, as DMs come from "Hubra"


def test_no_direct_message_unless_the_member_asked(app, db, tenant, trainer, seeded_db):
    from hubra.api.notifications.services import notify_member
    from hubra.core.tenant.context import in_club

    _conn(db, tenant, scopes=("notify",))
    fake = FakeDiscord()
    with in_club(tenant), patch.object(api, "bot", side_effect=fake):
        notify_member(seeded_db["admin"].id, "info", "New post", "x", topic="posts")
        db.session.commit()
    assert fake.calls == []


def test_notification_choices_list_discord_when_linked(
    client, db, club_auth, tenant, trainer
):
    _conn(db, tenant, scopes=("notify",))
    data = client.get("/api/v1/members/me/notifications", headers=H).get_json()
    assert data["apps"] == [{"key": "discord", "name": "Discord"}]
    assert data["topics"]["posts"]["discord"] is False
    resp = client.put(
        "/api/v1/members/me/notifications",
        json={"posts": {"discord": True}},
        headers=H,
    )
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["topics"]["posts"]["discord"] is True


def test_closed_direct_messages_are_not_an_error(app, db, tenant, trainer):
    from hubra.core.tenant.context import in_club

    conn = _conn(db, tenant, scopes=("notify",))
    with (
        in_club(tenant),
        patch.object(api, "bot", side_effect=FakeDiscord(fail_dm=403)),
    ):
        assert handler.deliver_notification(conn, "42", {"title": "x"}) is False


# ── Discord events ───────────────────────────────────────────────────────────


def test_events_follow_the_club(app, db, tenant):
    from hubra.core.models.event import ClubEvent
    from hubra.core.tenant.context import in_club
    from hubra_discord.tasks import sync_events

    conn = _conn(db, tenant, scopes=("events",))
    soon = date.today() + timedelta(days=5)
    with in_club(tenant):
        public = ClubEvent(
            title="Open day",
            date=soon,
            time="14:00-17:00",
            type="Social",
            is_public=True,
            location="Hall",
        )
        private = ClubEvent(title="Board", date=soon, type="Meeting", is_public=False)
        db.session.add_all([public, private])
        db.session.commit()
        public_id = public.id
    fake = FakeDiscord()
    with patch.object(api, "bot", side_effect=fake):
        assert sync_events.run(conn.id) == {
            "created": 1,
            "changed": 0,
            "deleted": 0,
            "members-only left out": 1,
        }
        (created,) = fake.sent("/scheduled-events")
        assert created["name"] == "Open day"
        assert created["entity_type"] == 3
        assert created["entity_metadata"] == {"location": "Hall"}
        assert created["scheduled_start_time"].startswith(soon.isoformat())
        # Nothing changed: Discord isn't called again.
        assert sync_events.run(conn.id) == {
            "created": 0,
            "changed": 0,
            "deleted": 0,
            "members-only left out": 1,
        }
        with in_club(tenant):
            db.session.get(ClubEvent, public_id).title = "Open day!"
            db.session.commit()
        assert sync_events.run(conn.id)["changed"] == 1
        with in_club(tenant):
            db.session.delete(db.session.get(ClubEvent, public_id))
            db.session.commit()
        assert sync_events.run(conn.id)["deleted"] == 1
    db.session.refresh(conn)
    assert conn.config["scheduled_events"] == {}


def test_changing_an_event_queues_the_sync(client, db, club_auth, tenant):
    from hubra.core.models.event import ClubEvent

    conn = _conn(db, tenant, scopes=("events",))
    event = ClubEvent(
        title="Fest",
        date=date.today() + timedelta(days=9),
        type="Social",
        is_public=True,
    )
    db.session.add(event)
    db.session.commit()
    with patch("hubra_discord.tasks.sync_events.delay") as queued:
        resp = client.patch(
            f"/api/v1/events/{event.id}", json={"title": "Fest 2"}, headers=H
        )
    assert resp.status_code == 200, resp.get_json()
    queued.assert_called_once_with(conn.id)


# ── Group channels ───────────────────────────────────────────────────────────


def test_group_channel_is_made_private(client, db, club_auth, tenant, trainer):
    conn = _conn(db, tenant, scopes=("channels",))
    fake = FakeDiscord()
    with (
        patch.object(api, "bot", side_effect=fake),
        patch("hubra_discord.tasks.sync_connection.delay") as sync,
    ):
        resp = client.patch(
            f"/api/v1/apps/connections/{conn.id}",
            json={"config": {"group_channels": [trainer.id]}},
            headers=H,
        )
    assert resp.status_code == 200, resp.get_json()
    (role,) = fake.sent(f"/guilds/{GUILD}/roles")
    assert role["name"] == "Trainer"
    (channel,) = fake.sent(f"/guilds/{GUILD}/channels")
    assert channel["name"] == "trainer"
    overwrites = {o["id"]: o for o in channel["permission_overwrites"]}
    assert int(overwrites[GUILD]["deny"]) & api.VIEW_CHANNEL
    assert APP_ID in overwrites
    sync.assert_called_once_with(conn.id)
    db.session.refresh(conn)
    (entry,) = conn.config["group_channels"]
    assert entry["group_id"] == trainer.id

    # Role sync now covers the channel's role.
    from hubra_discord.roles import managed_map

    assert managed_map(conn) == [
        {"source": f"group:{trainer.id}", "role_id": entry["role_id"]}
    ]


def test_group_event_goes_to_its_channel(client, db, club_auth, tenant, trainer):
    conn = _conn(
        db,
        tenant,
        scopes=("announce", "channels"),
        group_channels=[
            {"group_id": trainer.id, "channel_id": "777", "role_id": "778"}
        ],
    )
    with (
        patch("hubra_discord.tasks.send_to_group_channel.delay") as group,
        patch("hubra_discord.tasks.send_announcement.delay") as main,
    ):
        resp = client.post(
            "/api/v1/events",
            json={
                "title": "Team training",
                "date": (date.today() + timedelta(days=3)).isoformat(),
                "type": "Social",
                "group_id": trainer.id,
            },
            headers=H,
        )
    assert resp.status_code == 201, resp.get_json()
    group.assert_called_once()
    assert group.call_args.args[:2] == (conn.id, trainer.id)
    main.assert_not_called()


# ── Poll buttons ─────────────────────────────────────────────────────────────


def _poll(db, tenant, labels=("Ja", "Nein")):
    from hubra.core.models.poll import Poll, PollOption

    poll = Poll(question="Grillfest?", options=[PollOption(label=x) for x in labels])
    db.session.add(poll)
    db.session.commit()
    return poll


def test_vote_with_a_button(client, db, tenant, trainer, seeded_db):
    from hubra.core.models.poll import PollVote

    _conn(db, tenant)
    poll = _poll(db, tenant)
    click = {
        "type": 3,
        "guild_id": GUILD,
        "member": {"user": {"id": "42"}},
        "data": {"custom_id": f"vote:{poll.id}:1"},
    }
    data = _signed(client, click).get_json()["data"]
    assert "Nein" in data["content"] and data["flags"] == 64
    vote = db.session.query(PollVote).filter_by(poll_id=poll.id).one()
    assert vote.member_id == seeded_db["admin"].id
    # The activity list never says who voted for what.
    detail = db.session.query(ConnectedAppEvent).filter_by(action="voted").one().detail
    assert detail == f"#{poll.id}"


def test_poll_buttons(app, db, tenant):
    from hubra_discord.polls import buttons

    poll = _poll(db, tenant, labels=[f"Option {i}" for i in range(7)])
    rows = buttons(poll)
    assert [len(r["components"]) for r in rows] == [5, 2]
    assert rows[1]["components"][1]["custom_id"] == f"vote:{poll.id}:6"
    assert buttons(_poll(db, tenant, labels=[str(i) for i in range(26)])) is None


def test_group_poll_is_not_voted_by_others(client, db, tenant, trainer, seeded_db):
    from hubra.core.models.group import Group

    _conn(db, tenant)
    other = Group(name="Vorstand")
    db.session.add(other)
    db.session.commit()
    poll = _poll(db, tenant)
    poll.required_group_ids = [other.id]
    db.session.commit()
    click = {
        "type": 3,
        "guild_id": GUILD,
        "member": {"user": {"id": "42"}},
        "data": {"custom_id": f"vote:{poll.id}:0"},
    }
    assert "nur für" in _signed(client, click).get_json()["data"]["content"]


# ── Personal commands ────────────────────────────────────────────────────────


def test_invoices_command(client, db, tenant, trainer, seeded_db):
    from decimal import Decimal

    from hubra.core.models.finance import Invoice

    _conn(db, tenant)
    db.session.add(
        Invoice(
            member_id=seeded_db["admin"].id,
            issued=date.today(),
            due=date.today() - timedelta(days=1),
            amount=Decimal("1250.00"),
            status="Open",
        )
    )
    db.session.commit()
    text = _signed(client, _command("rechnungen")).get_json()["data"]["content"]
    assert "1'250.00" in text and "überfällig" in text
    stranger = _signed(client, _command("rechnungen", user="999")).get_json()
    assert "Verknüpfe" in stranger["data"]["content"]


def test_profile_command(client, db, tenant, trainer, seeded_db):
    _conn(db, tenant)
    text = _signed(client, _command("profil")).get_json()["data"]["content"]
    assert seeded_db["admin"].name in text and "Trainer" in text


def test_trainings_check_in(client, db, tenant, trainer, seeded_db):
    from datetime import datetime as dt

    from hubra.core.models.event import ClubEvent, EventAttendance
    from hubra.core.tasks.reminder_tasks import club_zone
    from hubra.core.tenant.context import in_club

    _conn(db, tenant)
    with in_club(tenant):
        now = dt.now(club_zone())
    start = (now + timedelta(minutes=30)).strftime("%H:%M")
    if (now + timedelta(minutes=30)).date() != now.date():
        pytest.skip("too close to midnight")
    event = ClubEvent(
        title="Abendtraining",
        date=now.date(),
        time=start,
        type="Training",
        category="training",
        group_id=trainer.id,
    )
    db.session.add(event)
    db.session.commit()
    data = _signed(client, _command("trainings")).get_json()["data"]
    assert "Abendtraining" in data["content"]
    ids = [b["custom_id"] for row in data["components"] for b in row["components"]]
    assert f"attend:{event.id}:present" in ids
    assert f"attend:{event.id}:excused" in ids

    click = {
        "type": 3,
        "guild_id": GUILD,
        "member": {"user": {"id": "42"}},
        "data": {"custom_id": f"attend:{event.id}:present"},
    }
    assert "Eingecheckt" in _signed(client, click).get_json()["data"]["content"]
    row = db.session.query(EventAttendance).filter_by(event_id=event.id).one()
    assert (row.member_id, row.status) == (seeded_db["admin"].id, "present")


def test_check_in_can_be_turned_off(client, db, tenant, trainer):
    _conn(db, tenant, self_checkin=False)
    click = {
        "type": 3,
        "guild_id": GUILD,
        "member": {"user": {"id": "42"}},
        "data": {"custom_id": "attend:1:present"},
    }
    assert "ausgeschaltet" in _signed(client, click).get_json()["data"]["content"]


def test_commands_include_the_personal_ones():
    from hubra_discord.commands import all_commands

    names = {c["name"] for c in all_commands()}
    assert {
        "events",
        "verbinden",
        "hubra",
        "rechnungen",
        "trainings",
        "profil",
    } <= names


# ── Permissions for scopes turned on later ───────────────────────────────────


def test_options_name_missing_permissions_and_a_grant_link(
    client, db, club_auth, tenant
):
    conn = _conn(db, tenant, scopes=("announce", "events"))

    def server(method, path, body=None, **_):
        if path.endswith("/roles"):
            return [
                {"id": GUILD, "name": "@everyone", "position": 0, "permissions": "0"},
                {
                    "id": "950",
                    "name": "Hubra",
                    "position": 5,
                    "managed": True,
                    "permissions": str(
                        api.VIEW_CHANNEL | api.SEND_MESSAGES | api.EMBED_LINKS
                    ),
                },
            ]
        return _server(method, path, body)

    with patch.object(api, "bot", side_effect=server):
        data = client.get(
            f"/api/v1/apps/connections/{conn.id}/options", headers=H
        ).get_json()
    assert data["missingPermissions"] == ["Change Nickname", "Create Events"]
    query = parse_qs(urlparse(data["grantUrl"]).query)
    assert query["guild_id"] == [GUILD]
    assert int(query["permissions"][0]) & api.CREATE_EVENTS


# ── Gateway (someone joined the server) ──────────────────────────────────────


def test_websocket_frames_round_trip():
    from hubra_discord.gateway import encode_frame, read_frame

    for size in (5, 300, 70000):
        payload = b"x" * size
        frame = encode_frame(payload, mask=b"abcd")
        buf = bytearray(frame)

        def read(n):
            out = bytes(buf[:n])
            del buf[:n]
            return out

        assert read_frame(read) == (True, 1, payload)


def test_gateway_hands_joins_to_the_worker():
    from hubra_discord.gateway import on_dispatch

    with patch("hubra_discord.tasks.member_joined.delay") as queued:
        on_dispatch("GUILD_MEMBER_ADD", {"guild_id": GUILD, "user": {"id": "77"}})
        on_dispatch(
            "GUILD_MEMBER_ADD", {"guild_id": GUILD, "user": {"id": "78", "bot": True}}
        )
        on_dispatch("MESSAGE_CREATE", {"guild_id": GUILD})
    queued.assert_called_once_with(GUILD, "77")


def test_gateway_session_resumes_and_reconnects():
    from hubra_discord.gateway import Closed, Session

    session = Session("t")
    session.handle(
        None,
        {
            "op": 0,
            "t": "READY",
            "s": 1,
            "d": {"session_id": "s", "resume_gateway_url": "wss://r"},
        },
    )
    assert (session.session_id, session.seq, session.resume_url) == ("s", 1, "wss://r")
    session.acked = False
    session.handle(None, {"op": 11})
    assert session.acked
    with pytest.raises(Closed):
        session.handle(None, {"op": 7})


def test_newcomer_gets_the_welcome(app, db, tenant):
    from hubra_discord.tasks import member_joined

    conn = _conn(
        db,
        tenant,
        scopes=("notify",),
        welcome_dm=True,
        welcome_text="Hallo bei {club}!",
    )
    fake = FakeDiscord()
    with patch.object(api, "bot", side_effect=fake):
        assert member_joined.run(GUILD, "555") == "welcome sent"
    (dm,) = fake.sent("/users/@me/channels")
    assert dm == {"recipient_id": "555"}
    (msg,) = fake.sent("/channels/dm-1/messages")
    assert msg["content"] == "Hallo bei Club A!"
    db.session.refresh(conn)


def test_no_welcome_unless_turned_on(app, db, tenant):
    from hubra_discord.tasks import member_joined

    _conn(db, tenant, scopes=("notify",))
    fake = FakeDiscord()
    with patch.object(api, "bot", side_effect=fake):
        assert member_joined.run(GUILD, "555") == ""
    assert fake.calls == []


def test_linked_member_joining_gets_roles(app, db, tenant, trainer, seeded_db):
    from hubra_discord.tasks import member_joined

    _mapped(db, tenant, trainer)
    server = FakeServer()
    with patch.object(api, "bot", side_effect=server):
        done = member_joined.run(GUILD, "42")
    assert "+Trainer" in done


# ── The platform's own server and Hubra's notices ────────────────────────────


def test_platform_clicks_go_to_the_platform_bot(client, db, tenant):
    from hubra.core.services import platform_bot

    click = {
        "type": 3,
        "guild_id": "999",
        "member": {"user": {"id": "42"}},
        "data": {"custom_id": "p:al:ack:relay_down"},
    }
    with (
        patch.object(platform_bot, "guild_id", return_value="999"),
        patch.object(
            platform_bot, "handle", return_value={"type": 4, "data": {"content": "ok"}}
        ) as handle,
    ):
        data = _signed(client, click).get_json()
    handle.assert_called_once()
    assert data["data"]["content"] == "ok"


def test_club_commands_wait_during_maintenance(client, db, tenant):
    from hubra.core.services import maintenance

    _conn(db, tenant)
    maintenance.turn_on("Update", 10)
    try:
        resp = _signed(client, _command("events"))
    finally:
        maintenance.turn_off()
    assert resp.status_code == 200
    assert "aktualisiert" in resp.get_json()["data"]["content"]


def test_a_window_for_this_club_pauses_its_commands(client, db, tenant):
    from datetime import datetime, timedelta, timezone

    from hubra.core.services import maintenance

    _conn(db, tenant)
    now = datetime.now(timezone.utc)
    window = maintenance.schedule(
        now + timedelta(minutes=1),
        now + timedelta(hours=1),
        "Umzug",
        tenant_ids=[tenant.id],
        notice_hours=0,
    )
    window_id = window["id"] if isinstance(window, dict) else window.id
    try:
        with patch.object(
            maintenance,
            "_running",
            lambda w, at: w["starts_at"] <= at + timedelta(minutes=2) < w["ends_at"],
        ):
            resp = _signed(client, _command("events"))
    finally:
        maintenance.cancel(window_id)
    assert "aktualisiert" in resp.get_json()["data"]["content"]


def test_hubra_notices_only_when_the_club_wants_them(app, db, tenant, queued):
    from hubra.core.tenant.context import in_club

    from hubra_discord.platform import notice_embed, platform_notice

    off = _conn(db, tenant)
    with in_club(tenant):
        assert platform_notice(off, "release", {"version": "1.2.3"}) is False
        off.config = {**off.config, "hubra_news": True}
        db.session.commit()
        assert platform_notice(off, "release", {"version": "1.2.3"}) is True
        # Maintenance has its own switch.
        assert platform_notice(off, "maintenance_started", {}) is False
        assert platform_notice(off, "something_else", {}) is False
        embed = notice_embed(
            "maintenance_planned",
            {"start": "2030-11-05T21:00:00+00:00", "minutes": 30, "message": "Update"},
        )
    queued.assert_called_once()
    conn_id, body, kind = queued.call_args.args[:3]
    assert conn_id == off.id and kind == "hubra"
    assert body["embeds"][0]["title"] == "Hubra 1.2.3"
    assert body["allowed_mentions"] == {"parse": []}
    assert "05.11.2030 22:00" in embed["description"]
    assert "30 Minuten" in embed["description"]


def test_platform_notice_toggles_are_saved(app, db, tenant):
    from hubra_discord.app import BOOLS, DEFAULT_CONFIG

    for key in ("hubra_news", "maintenance_notices"):
        assert key in BOOLS
        assert DEFAULT_CONFIG[key] is False


# ── Automatic group roles ─────────────────────────────────────────────────────


class GroupRoleServer:
    """A server whose roles the bot makes, renames and deletes."""

    def __init__(self):
        self.roles: dict[str, dict] = {ROLE_OTHER: {"id": ROLE_OTHER, "name": "Mods"}}
        self.member: set[str] = {ROLE_OTHER}
        self.calls: list = []
        self.next_id = 7000

    def __call__(self, method, path, body=None, **kw):
        self.calls.append((method, path, body))
        parts = path.strip("/").split("/")
        if parts[-1] == "roles" and method == "GET":
            return list(self.roles.values())
        if parts[-1] == "roles" and method == "POST":
            self.next_id += 1
            rid = str(self.next_id)
            self.roles[rid] = {"id": rid, **body}
            return self.roles[rid]
        if len(parts) == 4 and parts[2] == "roles":
            if method == "PATCH":
                self.roles[parts[3]].update(body)
                return self.roles[parts[3]]
            if method == "DELETE":
                self.roles.pop(parts[3], None)
                self.member.discard(parts[3])
                return None
        if path.endswith("/members/42") and method == "GET":
            return {"roles": sorted(self.member)}
        if "/members/42/roles/" in path:
            role = parts[-1]
            (self.member.add if method == "PUT" else self.member.discard)(role)
            return None
        raise AssertionError(path)

    def names(self) -> set[str]:
        return {self.roles[r]["name"] for r in self.member if r in self.roles}


def _group_roles_conn(db, tenant, **config):
    return _conn(db, tenant, group_roles=True, **config)


def test_every_group_gets_a_role(app, db, tenant, trainer):
    from hubra.core.models.group import Group

    from hubra_discord.tasks import sync_connection

    db.session.add(Group(name="Vorstand", visibility="private"))
    db.session.commit()
    conn = _group_roles_conn(db, tenant)
    server = GroupRoleServer()
    with patch.object(api, "bot", side_effect=server):
        assert sync_connection.run(conn.id) == 1
    made = {r["name"]: r for r in server.roles.values() if r["id"] != ROLE_OTHER}
    assert set(made) == {"Trainer"}  # the private group gets none
    assert made["Trainer"]["permissions"] == "0"
    assert not made["Trainer"]["mentionable"]
    assert server.names() == {"Mods", "Trainer"}
    db.session.refresh(conn)
    assert conn.config["auto_group_roles"][0]["group_id"] == trainer.id

    # Nothing new: no role is made twice.
    with patch.object(api, "bot", side_effect=server):
        sync_connection.run(conn.id)
    assert len(server.roles) == 2


def test_group_roles_follow_renames_and_privacy(app, db, tenant, trainer):
    from hubra_discord.tasks import sync_connection

    conn = _group_roles_conn(db, tenant)
    server = GroupRoleServer()
    with patch.object(api, "bot", side_effect=server):
        sync_connection.run(conn.id)
        trainer.name = "Leitung"
        db.session.commit()
        sync_connection.run(conn.id)
        assert server.names() == {"Mods", "Leitung"}
        assert len(server.roles) == 2

        trainer.visibility = "private"
        db.session.commit()
        sync_connection.run(conn.id)
    assert server.names() == {"Mods"}
    assert list(server.roles) == [ROLE_OTHER]


def test_turning_group_roles_off_deletes_them(app, db, tenant, trainer):
    from hubra_discord.tasks import sync_connection

    conn = _group_roles_conn(db, tenant)
    server = GroupRoleServer()
    with patch.object(api, "bot", side_effect=server):
        sync_connection.run(conn.id)
        conn.config = {**conn.config, "group_roles": False}
        db.session.commit()
        sync_connection.run(conn.id)
    assert list(server.roles) == [ROLE_OTHER]
    db.session.refresh(conn)
    assert conn.config["auto_group_roles"] == []


def test_mapped_groups_keep_their_own_role(app, db, tenant, trainer):
    from hubra_discord.tasks import sync_connection

    conn = _group_roles_conn(
        db,
        tenant,
        role_map=[{"source": f"group:{trainer.id}", "role_id": ROLE_OTHER}],
    )
    server = GroupRoleServer()
    with patch.object(api, "bot", side_effect=server):
        sync_connection.run(conn.id)
    assert list(server.roles) == [ROLE_OTHER]  # no extra role


def test_group_roles_need_the_roles_scope(app, db, tenant):
    from hubra.core import apps

    from hubra_discord.app import handler

    conn = _conn(db, tenant, scopes=("announce",))
    with pytest.raises(apps.AppError):
        handler.update_config(conn, {"group_roles": True})


def test_joining_the_platform_server_gives_platform_roles(app, db):
    pytest.importorskip("hubra.core.services.platform_roles")  # Hubra after #149
    from hubra_discord.tasks import member_joined

    with patch(
        "hubra.core.services.platform_roles.joined", return_value=True
    ) as joined:
        assert member_joined.run("999", "42") == "platform roles"
    joined.assert_called_once_with("999", "42")


# ── Messages kept up to date (live.py) ───────────────────────────────────────


def _future(days: int = 10) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _calls(bot, method: str) -> list:
    return [c for c in bot.call_args_list if c.args[0] == method]


def test_event_message_is_remembered_and_shows_places(client, db, club_auth, tenant):
    from hubra_discord import live
    from hubra_discord.tasks import refresh_event, send_announcement

    conn = _conn(db, tenant)
    with patch("hubra_discord.tasks.send_announcement.delay") as delay:
        resp = client.post(
            "/api/v1/events/",
            json={
                "title": "Quiz night",
                "date": _future(),
                "time": "19:00-22:00",
                "type": "Social",
                "location": "Clubhouse",
                "max_attendees": 20,
                "is_public": True,
            },
            headers=H,
        )
    event_id = resp.get_json()["id"]
    args = delay.call_args.args
    assert args[3] == f"e:{event_id}"
    embed = args[1]["embeds"][0]
    fields = {f["name"]: f["value"] for f in embed["fields"]}
    # A Discord timestamp: everyone sees it in their own time zone.
    assert fields["🗓️ Wann"].startswith("<t:") and ":R>" in fields["🗓️ Wann"]
    assert fields["📍 Wo"] == "Clubhouse"
    assert fields["👥 Plätze"] == "0 / 20 · 20 frei"

    with patch.object(api, "bot", return_value={"id": "m1"}):
        send_announcement.run(*args)
    db.session.refresh(conn)
    assert live.posted(conn)[f"e:{event_id}"] == [[CHANNEL, "m1"]]

    # A sign-up in the app edits the message (newer Hubra sends the event).
    from hubra.core.modules import events as core_events

    if hasattr(core_events, "EVENT_SIGNUPS_CHANGED"):
        with patch("hubra_discord.tasks.refresh_event.delay") as refresh:
            client.post(f"/api/v1/events/{event_id}/register", json={}, headers=H)
        refresh.assert_called_with(conn.id, event_id, False)

    with patch.object(api, "bot", return_value={}) as bot:
        assert refresh_event.run(conn.id, event_id) == 1
    method, path, body = bot.call_args.args
    assert (method, path) == ("PATCH", f"/channels/{CHANNEL}/messages/m1")
    places = {f["name"]: f["value"] for f in body["embeds"][0]["fields"]}["👥 Plätze"]
    assert places.startswith(
        "1 / 20" if hasattr(core_events, "EVENT_SIGNUPS_CHANGED") else "0 / 20"
    )
    assert body["components"][0]["components"][0]["custom_id"] == f"register:{event_id}"


def test_deleted_event_is_marked_cancelled(client, db, club_auth, tenant):
    from hubra_discord import live
    from hubra_discord.tasks import refresh_event

    conn = _conn(db, tenant)
    resp = client.post(
        "/api/v1/events/",
        json={"title": "BBQ", "date": _future(), "type": "Social", "is_public": True},
        headers=H,
    )
    event_id = resp.get_json()["id"]
    live.remember(conn, live.event_key(event_id), CHANNEL, "m2")
    db.session.commit()
    with patch("hubra_discord.tasks.refresh_event.delay") as refresh:
        client.delete(f"/api/v1/events/{event_id}", headers=H)
    refresh.assert_called_with(conn.id, event_id, True)

    old = {
        "embeds": [{"title": "📅 BBQ", "fields": [{"name": "👥 Plätze", "value": "3"}]}]
    }

    def server(method, path, body=None, **_):
        return old if method == "GET" else {}

    with patch.object(api, "bot", side_effect=server) as bot:
        assert refresh_event.run(conn.id, event_id, True) == 1
    body = _calls(bot, "PATCH")[0].args[2]
    assert body["embeds"][0]["title"] == "❌ Abgesagt: BBQ"
    assert body["embeds"][0]["fields"] == []
    assert body["components"] == []
    db.session.refresh(conn)
    assert live.posted(conn) == {}


def test_message_deleted_on_discord_is_forgotten(db, tenant):
    from hubra.core.models.event import ClubEvent
    from hubra_discord import live
    from hubra_discord.tasks import refresh_event

    conn = _conn(db, tenant)
    event = ClubEvent(
        title="Gone", date=date.today() + timedelta(days=3), type="Social"
    )
    db.session.add(event)
    db.session.commit()
    live.remember(conn, live.event_key(event.id), CHANNEL, "m3")
    db.session.commit()
    with patch.object(api, "bot", side_effect=DiscordError("Unknown Message", 404)):
        assert refresh_event.run(conn.id, event.id) == 0
    db.session.refresh(conn)
    assert live.posted(conn) == {}


def test_closed_poll_shows_its_result(client, db, club_auth, tenant):
    from hubra_discord import live
    from hubra_discord.tasks import finish_poll, tidy_messages

    conn = _conn(db, tenant)
    resp = client.post(
        "/api/v1/polls/",
        json={"question": "Pizza?", "options": [{"label": "Yes"}, {"label": "No"}]},
        headers=H,
    )
    poll_id = resp.get_json()["id"]
    voted = client.post(
        f"/api/v1/polls/{poll_id}/vote", json={"option_index": 0}, headers=H
    )
    assert voted.status_code == 200, voted.get_json()
    live.remember(conn, live.poll_key(poll_id), CHANNEL, "m4")
    db.session.commit()

    # Still open: nothing to do.
    with patch("hubra_discord.tasks.finish_poll.delay") as later:
        tidy_messages.run()
    later.assert_not_called()

    with patch("hubra_discord.tasks.finish_poll.delay") as now:
        client.patch(f"/api/v1/polls/{poll_id}/close", headers=H)
    from hubra.core.modules import events as core_events

    if hasattr(core_events, "POLL_CLOSED"):
        now.assert_called_with(conn.id, poll_id)
    with patch("hubra_discord.tasks.finish_poll.delay") as later:
        tidy_messages.run()
    later.assert_called_with(conn.id, poll_id)

    with patch.object(api, "bot", return_value={"id": "m5"}) as bot:
        assert finish_poll.run(conn.id, poll_id) == 1
    edit = _calls(bot, "PATCH")[0].args
    assert edit[1] == f"/channels/{CHANNEL}/messages/m4"
    result = edit[2]["embeds"][0]
    assert result["title"] == "✅ Beendet: Pizza?"
    assert "🏆 **Yes**" in result["description"]
    assert edit[2]["components"] == []
    notice = _calls(bot, "POST")[0].args[2]
    assert notice["content"].startswith("🗳️ **Umfrage beendet:** Pizza?")
    assert notice["message_reference"]["message_id"] == "m4"
    assert notice["allowed_mentions"] == {"parse": []}
    db.session.refresh(conn)
    assert live.posted(conn) == {}


def test_posted_list_stays_small(db, tenant):
    from hubra_discord import live

    conn = _conn(db, tenant)
    for i in range(live.MAX_KEYS + 5):
        live.remember(conn, f"e:{i}", CHANNEL, str(i))
    known = live.posted(conn)
    assert len(known) == live.MAX_KEYS
    assert "e:0" not in known and f"e:{live.MAX_KEYS + 4}" in known


# ── Doing club work from Discord (create.py) ─────────────────────────────────


def _with(name, user="42", **options):
    cmd = _command(name, user=user)
    cmd["data"]["options"] = [{"name": k, "value": v} for k, v in options.items()]
    return cmd


def test_dates_and_times_as_people_type_them():
    from hubra_discord.create import parse_closing, parse_date, parse_time

    today = date(2026, 12, 20)
    assert parse_date("heute", today) == today
    assert parse_date("morgen", today) == date(2026, 12, 21)
    assert parse_date("24.12.2026", today) == date(2026, 12, 24)
    assert parse_date("3.1.", today) == date(2027, 1, 3)  # next one
    assert parse_date("31.02.2026", today) is None
    assert parse_time("19.00") == "19:00"
    assert parse_time("9:30 - 11:00") == "09:30-11:00"
    assert parse_time("") == "" and parse_time("abends") is None
    assert parse_closing("") is None and parse_closing("bald") is False
    assert parse_closing("24.12.2030 18:00").hour == 18


def test_new_event_from_discord(client, db, tenant, trainer, queued):
    from hubra.core.models.event import ClubEvent

    _conn(db, tenant)
    day = (date.today() + timedelta(days=5)).strftime("%d.%m.%Y")
    text = _signed(
        client,
        _with(
            "anlass",
            titel="Grillabend",
            datum=day,
            zeit="18:00-22:00",
            ort="Clubhaus",
            oeffentlich=True,
        ),
    ).get_json()["data"]["content"]
    assert "Grillabend" in text and "erstellt" in text
    event = db.session.query(ClubEvent).filter_by(title="Grillabend").one()
    assert (
        event.time == "18:00-22:00" and event.is_public and event.location == "Clubhaus"
    )
    assert queued.called  # announced like one made in the app


def test_new_event_needs_a_linked_member_and_a_readable_date(
    client, db, tenant, trainer
):
    _conn(db, tenant)
    text = _signed(
        client, _with("anlass", user="999", titel="X", datum="morgen")
    ).get_json()
    assert "Verknüpfe" in text["data"]["content"]
    text = _signed(client, _with("anlass", titel="X", datum="irgendwann")).get_json()
    assert "Datum verstehe ich nicht" in text["data"]["content"]


def test_new_poll_from_discord(client, db, tenant, trainer):
    from hubra.core.models.poll import Poll

    _conn(db, tenant)
    text = _signed(
        client,
        _with("umfrage", frage="Pizza oder Pasta?", optionen="Pizza; Pasta; Egal"),
    ).get_json()["data"]["content"]
    assert "gestartet" in text
    poll = db.session.query(Poll).filter_by(question="Pizza oder Pasta?").one()
    assert [o.label for o in poll.options] == ["Pizza", "Pasta", "Egal"]
    text = _signed(client, _with("umfrage", frage="?", optionen="nur eine")).get_json()
    assert "mindestens zwei" in text["data"]["content"]


def test_circular_resolution_for_a_function(client, db, tenant, trainer, seeded_db):
    from hubra.core.models.function import Function, FunctionHolder
    from hubra.core.models.poll import Poll

    _conn(db, tenant)
    board = Function(name="Vorstand")
    db.session.add(board)
    db.session.flush()
    db.session.add(
        FunctionHolder(function_id=board.id, member_id=seeded_db["admin"].id)
    )
    db.session.commit()

    typing = {
        "type": 4,
        "guild_id": GUILD,
        "member": {"user": {"id": "42"}},
        "data": {
            "name": "beschluss",
            "options": [{"name": "funktion", "value": "vor", "focused": True}],
        },
    }
    choices = _signed(client, typing).get_json()["data"]["choices"]
    assert choices == [{"name": "Vorstand", "value": str(board.id)}]

    text = _signed(
        client, _with("beschluss", frage="Neue Tore kaufen?", funktion=str(board.id))
    ).get_json()["data"]["content"]
    assert "gestartet" in text
    poll = db.session.query(Poll).filter_by(question="Neue Tore kaufen?").one()
    assert [o.label for o in poll.options] == ["Ja", "Nein", "Enthaltung"]
    assert poll.is_required and poll.required_function_ids == [board.id]
    if hasattr(poll, "auto_close"):
        assert poll.auto_close is True


def test_groups_command_joins_and_leaves(client, db, tenant, trainer, seeded_db):
    from hubra.core.models.group import Group

    _conn(db, tenant)
    choir = Group(name="Chor", visibility="open")
    db.session.add(choir)
    db.session.commit()
    answer = _signed(client, _command("gruppen")).get_json()
    assert "Chor" in answer["data"]["content"]
    button = answer["data"]["components"][0]["components"][0]
    assert button["custom_id"] == f"gjoin:{choir.id}"
    click = {
        "type": 3,
        "guild_id": GUILD,
        "member": {"user": {"id": "42"}},
        "data": {"custom_id": f"gjoin:{choir.id}"},
    }
    answer = _signed(client, click).get_json()
    assert answer["type"] == 7 and "jetzt in **Chor**" in answer["data"]["content"]
    assert seeded_db["admin"] in db.session.get(Group, choir.id).members


def test_help_lists_what_the_person_may_do(client, db, tenant, trainer):
    _conn(db, tenant)
    text = _signed(client, _command("hilfe")).get_json()["data"]["content"]
    assert "/anlass" in text and "/gruppen" in text
    stranger = _signed(client, _command("hilfe", user="999")).get_json()["data"][
        "content"
    ]
    assert "/anlass" not in stranger and "Verknüpfe" in stranger


def test_maybe_button(client, db, tenant, trainer, seeded_db):
    from hubra.api.events.services import EventService
    from hubra.core.models.event import ClubEvent, EventRegistration
    from hubra_discord.commands import event_buttons

    if not hasattr(EventService, "maybe"):
        pytest.skip("Hubra without 'Vielleicht'")
    _conn(db, tenant)
    event = ClubEvent(
        title="Quiz",
        date=date.today() + timedelta(days=2),
        type="Social",
        is_public=True,
    )
    db.session.add(event)
    db.session.commit()
    labels = [b["label"] for b in event_buttons(event)[0]["components"]]
    assert labels == ["Anmelden", "Vielleicht", "Abmelden"]
    click = {
        "type": 3,
        "guild_id": GUILD,
        "member": {"user": {"id": "42"}},
        "data": {"custom_id": f"maybe:{event.id}"},
    }
    assert "Vielleicht" in _signed(client, click).get_json()["data"]["content"]
    reg = db.session.query(EventRegistration).filter_by(event_id=event.id).one()
    assert reg.status == "maybe"


# ── Mentioning those who still have to vote (nudge.py) ───────────────────────


def test_closing_post_mentions_linked_non_voters(app, db, tenant, trainer, seeded_db):
    from hubra.core.models.poll import Poll, PollOption
    from hubra.core.tenant.context import in_club
    from hubra_discord.nudge import add_mentions

    conn = _conn(db, tenant, nudge_voters=True)
    with in_club(tenant):
        poll = Poll(
            question="Trikots?", is_required=True, required_group_ids=[trainer.id]
        )
        poll.options = [PollOption(label="Ja"), PollOption(label="Nein")]
        db.session.add(poll)
        db.session.commit()
        body = add_mentions(
            conn, poll, {"embeds": [], "allowed_mentions": {"parse": []}}
        )
    assert body["content"] == "🗳️ Noch nicht abgestimmt: <@42>"
    assert body["allowed_mentions"] == {"parse": [], "users": ["42"]}

    conn.config = {**conn.config, "nudge_voters": False}
    with in_club(tenant):
        assert "content" not in add_mentions(conn, poll, {"embeds": []})


# ── The server from Hubra (server.py, routes) ────────────────────────────────


class ChannelServer:
    """A small Discord server: two categories, channels, one made by Hubra."""

    def __init__(self):
        self.calls = []
        self.channels = [
            {"id": "800", "name": "Info", "type": 4, "position": 0},
            {
                "id": CHANNEL,
                "name": "news",
                "type": 0,
                "parent_id": "800",
                "position": 0,
            },
            {"id": "903", "name": "Teams", "type": 4, "position": 1},
            {
                "id": "904",
                "name": "u17",
                "type": 0,
                "parent_id": "903",
                "position": 0,
                "permission_overwrites": [
                    {"id": GUILD, "type": 0, "deny": str(1 << 10), "allow": "0"}
                ],
            },
            {
                "id": "905",
                "name": "Garderobe",
                "type": 2,
                "parent_id": "903",
                "position": 1,
            },
        ]

    def __call__(self, method, path, body=None, **kw):
        self.calls.append((method, path, body))
        if path == f"/guilds/{GUILD}/channels" and method == "GET":
            return self.channels
        if path == f"/guilds/{GUILD}/channels" and method == "POST":
            made = {"id": "999", "name": body["name"], "type": body["type"]}
            self.channels.append(made)
            return made
        if path.endswith("/roles"):
            return [
                {"id": GUILD, "permissions": str((1 << 10) | (1 << 11) | (1 << 14))},
                {"id": "950", "permissions": str(1 << 4), "managed": True},
            ]
        if path.endswith(f"/members/{APP_ID}"):
            return {"roles": ["950"]}
        if path.startswith("/channels/") and method == "GET":
            cid = path.split("/")[2]
            found = next((c for c in self.channels if c["id"] == cid), None)
            return {**found, "guild_id": GUILD} if found else None
        if path.startswith("/channels/") and method in ("PATCH", "DELETE"):
            return {"id": path.split("/")[2], **(body or {})}
        raise AssertionError((method, path))


def test_channels_overview(client, db, club_auth, tenant):
    conn = _conn(db, tenant, scopes=("announce", "channels"), made_channels=["904"])
    with patch.object(api, "bot", side_effect=ChannelServer()):
        data = client.get(
            f"/api/v1/discord/connections/{conn.id}/channels", headers=H
        ).get_json()
    names = [c["name"] for c in data["channels"]]
    # Discord's order: category, then its text channels, then voice.
    assert names == ["Info", "news", "Teams", "u17", "Garderobe"]
    by = {c["name"]: c for c in data["channels"]}
    assert by["news"]["uses"] == ["Hauptkanal"] and by["news"]["botCanPost"]
    assert (
        by["u17"]["private"] and by["u17"]["madeByHubra"] and not by["u17"]["botCanSee"]
    )
    assert data["canManage"] is True


def test_make_rename_and_delete_a_channel(client, db, club_auth, tenant):
    conn = _conn(db, tenant, scopes=("announce", "channels"))
    server = ChannelServer()
    base = f"/api/v1/discord/connections/{conn.id}/channels"
    with patch.object(api, "bot", side_effect=server):
        resp = client.post(
            base,
            json={"name": "Vorstand Intern", "parentId": "903", "private": True},
            headers=H,
        )
        assert resp.status_code == 201, resp.get_json()
        assert resp.get_json()["name"] == "vorstand-intern"
        made = [c for c in server.calls if c[0] == "POST"][0][2]
        assert made["parent_id"] == "903"
        assert made["permission_overwrites"][0] == {
            "id": GUILD,
            "type": 0,
            "deny": str(1 << 10),
            "allow": "0",
        }
        assert (
            client.patch(
                f"{base}/999", json={"name": "Vorstand"}, headers=H
            ).status_code
            == 200
        )
        assert client.delete(f"{base}/999", headers=H).status_code == 200
        # People's own channels and the ones Hubra posts in stay.
        resp = client.delete(f"{base}/905", headers=H)
        assert (
            resp.status_code == 409 and "selbst angelegt" in resp.get_json()["message"]
        )
    db.session.refresh(conn)
    assert "999" not in conn.config.get("made_channels", [])


def test_channel_changes_need_the_channels_scope(client, db, club_auth, tenant):
    conn = _conn(db, tenant, scopes=("announce",))
    with patch.object(api, "bot", side_effect=ChannelServer()):
        resp = client.post(
            f"/api/v1/discord/connections/{conn.id}/channels",
            json={"name": "x"},
            headers=H,
        )
    assert resp.status_code == 409 and "Kanäle" in resp.get_json()["message"]


def test_saving_settings_keeps_what_the_bot_remembers(client, db, club_auth, tenant):
    conn = _conn(db, tenant, posted={"e:1": [[CHANNEL, "5"]]}, made_channels=["904"])
    with patch.object(api, "bot", side_effect=_server):
        resp = client.patch(
            f"/api/v1/apps/connections/{conn.id}",
            json={"config": {"announce_posts": False}},
            headers=H,
        )
    assert resp.status_code == 200
    db.session.refresh(conn)
    assert conn.config["posted"] == {"e:1": [[CHANNEL, "5"]]}
    assert conn.config["made_channels"] == ["904"]


def test_posted_messages_list(client, db, club_auth, tenant):
    from hubra.core.models.event import ClubEvent

    event = ClubEvent(
        title="Turnier", date=date.today() + timedelta(days=9), type="Social"
    )
    db.session.add(event)
    db.session.commit()
    conn = _conn(
        db,
        tenant,
        posted={f"e:{event.id}": [[CHANNEL, "77"]], "p:9999": [[CHANNEL, "78"]]},
    )
    data = client.get(
        f"/api/v1/discord/connections/{conn.id}/messages", headers=H
    ).get_json()
    first, second = data["messages"]
    assert first["title"] == "(gelöschte Umfrage)" and not first["exists"]
    assert second["title"] == "Turnier"
    assert (
        second["messages"][0]["url"]
        == f"https://discord.com/channels/{GUILD}/{CHANNEL}/77"
    )
    resp = client.delete(
        f"/api/v1/discord/connections/{conn.id}/messages/p:9999", headers=H
    )
    assert resp.status_code == 200
    db.session.refresh(conn)
    assert "p:9999" not in conn.config["posted"]


def test_server_api_needs_the_right(client, db, tenant):
    conn = _conn(db, tenant)
    assert client.get(
        f"/api/v1/discord/connections/{conn.id}/channels", headers=H
    ).status_code in (401, 403)


# ── Channel layout, permission audit, member role, DMs, group channels ──────


class LayoutServer(ChannelServer):
    """ChannelServer that also makes roles and takes permission overwrites."""

    def __init__(self, everyone_bits=(1 << 10) | (1 << 11) | (1 << 14)):
        super().__init__()
        self.everyone_bits = everyone_bits
        self.roles = [
            {"id": GUILD, "name": "@everyone", "permissions": str(everyone_bits)},
            {"id": "950", "name": "Hubra", "permissions": str(1 << 4), "managed": True},
        ]
        self.next_id = 2000

    def _id(self):
        self.next_id += 1
        return str(self.next_id)

    def __call__(self, method, path, body=None, **kw):
        if path == f"/guilds/{GUILD}/channels" and method == "POST":
            self.calls.append((method, path, body))
            made = {"id": self._id(), "guild_id": GUILD, **body}
            self.channels.append(made)
            return made
        if path == f"/guilds/{GUILD}/roles" and method == "GET":
            self.calls.append((method, path, body))
            return self.roles
        if path == f"/guilds/{GUILD}/roles" and method == "POST":
            self.calls.append((method, path, body))
            role = {"id": self._id(), "name": body["name"], "permissions": "0"}
            self.roles.append(role)
            return role
        if "/permissions/" in path and method == "PUT":
            self.calls.append((method, path, body))
            cid, oid = path.split("/")[2], path.split("/")[4]
            channel = next(c for c in self.channels if c["id"] == cid)
            kept = [
                o for o in channel.get("permission_overwrites") or [] if o["id"] != oid
            ]
            channel["permission_overwrites"] = [*kept, {"id": oid, **body}]
            return None
        if path.endswith("/messages") and method == "POST":
            self.calls.append((method, path, body))
            return {"id": "m1"}
        return super().__call__(method, path, body, **kw)


def test_layout_makes_channels_with_the_right_permissions(
    client, db, club_auth, tenant, discord_settings
):
    conn = _conn(
        db, tenant, scopes=("announce", "channels", "commands"), channel_id=None
    )
    server = LayoutServer()
    base = f"/api/v1/discord/connections/{conn.id}/layout"
    with patch.object(api, "bot", side_effect=server):
        resp = client.post(
            base,
            json={
                "slots": ["rules", "announcements", "roles", "events", "public_events"],
                "categoryName": "Verein",
            },
            headers=H,
        )
        assert resp.status_code == 201, resp.get_json()
        made = {m["slot"]: m for m in resp.get_json()["made"]}
        assert set(made) == {
            "category",
            "rules",
            "announcements",
            "roles",
            "events",
            "public_events",
        }
        state = client.get(base, headers=H).get_json()
    db.session.refresh(conn)
    config = conn.config
    role = config["auto_member_role"]["role_id"]
    assert config["member_role"] is True
    by_id = {c["id"]: c for c in server.channels}
    # Everyone reads the rules; nobody but the bot writes there.
    rules = by_id[made["rules"]["id"]]
    everyone = next(o for o in rules["permission_overwrites"] if o["id"] == GUILD)
    assert int(everyone["allow"]) & api.VIEW_CHANNEL
    assert int(everyone["deny"]) & api.SEND_MESSAGES
    # Events are for members: @everyone can't see them, the member role can.
    events = by_id[made["events"]["id"]]
    overwrites = {o["id"]: o for o in events["permission_overwrites"]}
    assert int(overwrites[GUILD]["deny"]) & api.VIEW_CHANNEL
    assert int(overwrites[role]["allow"]) & api.VIEW_CHANNEL
    assert all(c.get("parent_id") == made["category"]["id"] for c in (rules, events))
    # Wired up in Hubra, public ones approved.
    assert config["channel_id"] == made["announcements"]["id"]
    assert config["channels"]["events"] == made["events"]["id"]
    assert config["channels"]["public_events"] == made["public_events"]["id"]
    assert config["members_only_events"] is True
    assert {a["id"] for a in config["public_ok"]} == {
        made["rules"]["id"],
        made["public_events"]["id"],
    }
    # The roles channel got its "Gruppen wählen" button.
    panel = [b for m, p, b in server.calls if p.endswith("/messages")][0]
    assert panel["components"][0]["components"][0]["custom_id"] == "gpick"
    assert {s["key"]: s["channelId"] for s in state["slots"]}["rules"] == made["rules"][
        "id"
    ]
    # Again: nothing new.
    with patch.object(api, "bot", side_effect=server):
        again = client.post(base, json={"slots": ["rules"]}, headers=H).get_json()
    assert again["made"] == [] and again["kept"] == ["rules"]


def test_audit_flags_public_channels_until_approved_or_fixed(
    client, db, club_auth, tenant, discord_settings
):
    conn = _conn(db, tenant, scopes=("announce", "channels"))
    server = LayoutServer(everyone_bits=(1 << 10) | (1 << 17))
    base = f"/api/v1/discord/connections/{conn.id}/audit"
    with patch.object(api, "bot", side_effect=server):
        result = client.get(base, headers=H).get_json()
        rows = {c["id"]: c for c in result["channels"]}
        assert rows[CHANNEL]["access"] == "public"
        assert rows["904"]["access"] == "private"
        assert {f["channelId"] for f in result["findings"]} == {CHANNEL, "905"}
        assert any("@everyone erwähnen" in t for t in result["server"])
        # Approve one: no longer a finding.
        result = client.post(
            f"{base}/{CHANNEL}", json={"action": "approve"}, headers=H
        ).get_json()
        assert {f["channelId"] for f in result["findings"]} == {"905"}
        assert {c["id"]: c for c in result["channels"]}[CHANNEL]["approved"]
        # Fix the other: members only, with the member role made for it.
        result = client.post(
            f"{base}/905", json={"action": "members_only"}, headers=H
        ).get_json()
        assert result["findings"] == []
        assert {c["id"]: c for c in result["channels"]}["905"]["access"] == "members"
    db.session.refresh(conn)
    assert conn.config["member_role"] is True
    from hubra.core.models.audit import AuditEntry

    actions = {a.action for a in db.session.query(AuditEntry).all()}
    assert {
        "discord.channel_public_approved",
        "discord.channel_members_only",
    } <= actions


def test_nightly_audit_reports_a_finding_once(app, db, tenant, discord_settings):
    from hubra.core.tenant.context import in_club

    from hubra_discord.audit import nightly

    conn = _conn(db, tenant, scopes=("announce", "channels"))
    server = LayoutServer()
    with in_club(tenant), patch.object(api, "bot", side_effect=server):
        assert nightly(conn) == 2  # #news and the voice channel
        assert nightly(conn) == 0


def test_group_and_function_channels_in_a_category(
    client, db, club_auth, tenant, discord_settings
):
    from hubra.core.models.function import Function
    from hubra.core.models.group import Group

    conn = _conn(db, tenant, scopes=("announce", "channels"))
    u17 = Group(name="U17", visibility="open")
    board = Function(name="Vorstand")
    db.session.add_all([u17, board])
    db.session.commit()
    server = LayoutServer()
    url = f"/api/v1/apps/connections/{conn.id}"
    wanted = [
        {"source": f"group:{u17.id}", "categoryId": "903"},
        {"source": f"group:{u17.id}", "name": "u17-eltern", "categoryId": "903"},
        {"source": f"function:{board.id}"},
    ]
    with (
        patch.object(api, "bot", side_effect=server),
        patch("hubra_discord.tasks.sync_connection.delay"),
    ):
        resp = client.patch(url, json={"config": {"group_channels": wanted}}, headers=H)
        assert resp.status_code == 200, resp.get_json()
    db.session.refresh(conn)
    entries = conn.config["group_channels"]
    assert [e["channel_name"] for e in entries] == ["u17", "u17-eltern", "vorstand"]
    # One role per group, shared by its channels.
    assert entries[0]["role_id"] == entries[1]["role_id"] != entries[2]["role_id"]
    assert entries[0]["category_id"] == "903" and entries[2]["category_id"] is None
    made = [b for m, p, b in server.calls if m == "POST" and p.endswith("/channels")]
    assert made[0]["parent_id"] == "903" and "parent_id" not in made[2]
    # Move the board's channel into a category; keep the others.
    keep = [{"source": e["source"], "channelId": e["channel_id"]} for e in entries]
    keep[0]["categoryId"] = keep[1]["categoryId"] = "903"
    keep[2]["categoryId"] = "800"
    with (
        patch.object(api, "bot", side_effect=server),
        patch("hubra_discord.tasks.sync_connection.delay"),
    ):
        resp = client.patch(url, json={"config": {"group_channels": keep}}, headers=H)
        assert resp.status_code == 200, resp.get_json()
    moved = [b for m, p, b in server.calls if m == "PATCH"]
    assert moved == [{"parent_id": "800"}]
    db.session.refresh(conn)
    assert len(conn.config["group_channels"]) == 3


def test_function_polls_go_to_the_function_channel(app, db, tenant):
    from hubra.core.tenant.context import in_club

    from hubra_discord.hooks import _to_groups

    conn = _conn(
        db,
        tenant,
        scopes=("announce", "channels"),
        group_channels=[
            {
                "source": "function:7",
                "function_id": 7,
                "channel_id": "777",
                "role_id": "70",
                "name": "Vorstand",
            }
        ],
    )
    with (
        in_club(tenant),
        patch("hubra_discord.hooks.after_commit") as later,
    ):
        _to_groups(
            tenant.id,
            [],
            lambda c: ({"title": "Beschluss"}, None),
            "Poll",
            function_ids=[7],
        )
    args = later.call_args.args
    assert args[1] == conn.id and args[-1] == "function:7"


def test_public_events_go_to_the_public_channel(app, db, tenant):
    from hubra.core.tenant.context import in_club

    from hubra_discord.hooks import _announce

    _conn(
        db,
        tenant,
        scopes=("announce",),
        channels={"events": "801", "public_events": "802"},
    )
    with in_club(tenant), patch("hubra_discord.hooks.after_commit") as later:
        _announce(
            tenant.id,
            "events",
            "announce_events",
            lambda c: {"title": "x"},
            public=True,
        )
        _announce(tenant.id, "events", "announce_events", lambda c: {"title": "y"})
    kinds = [c.args[3] for c in later.call_args_list]
    assert kinds == ["public_events", "events"]


def test_role_sync_follows_hubra_roles_and_membership_types(app, db, tenant, seeded_db):
    from hubra.core.models.role import Role
    from hubra.core.tenant.context import in_club

    from hubra_discord.roles import matches, sources

    with in_club(tenant):
        admin = seeded_db["admin"]
        admin.status = "active"
        admin.membership_type = "honorary"
        role = Role(name="Kassier")
        db.session.add(role)
        admin.roles.append(role)
        db.session.commit()
        keys = {s["key"] for s in sources()}
        assert f"role:{role.id}" in keys and "type:honorary" in keys
        assert matches(f"role:{role.id}", admin)
        assert matches("type:honorary", admin)
        assert not matches("type:passive", admin)


def test_member_role_is_made_and_given(app, db, tenant, trainer, discord_settings):
    from hubra.core.tenant.context import in_club

    from hubra_discord import roles

    conn = _conn(db, tenant, scopes=("announce", "roles"), member_role=True)
    server = LayoutServer()
    given = []
    with (
        in_club(tenant),
        patch.object(api, "bot", side_effect=server),
        patch.object(api, "member", return_value={"roles": []}),
        patch.object(api, "add_role", side_effect=lambda g, u, r, why: given.append(r)),
    ):
        roles.sync_all(conn)
    role = conn.config["auto_member_role"]["role_id"]
    assert given == [role]
    assert any(r["name"] == "Mitglied" for r in server.roles)


def test_dm_to_a_group(client, db, club_auth, tenant, trainer, discord_settings):
    conn = _conn(db, tenant, scopes=("announce", "notify"))
    base = f"/api/v1/discord/connections/{conn.id}/dm"
    source = f"group:{trainer.id}"
    assert client.get(f"{base}?source={source}", headers=H).get_json()["count"] == 1
    with patch("hubra_discord.tasks.send_dm_broadcast.delay") as queued:
        resp = client.post(
            base,
            json={"source": source, "title": "Training fällt aus", "text": "Regen."},
            headers=H,
        )
        assert resp.status_code == 202, resp.get_json()
    assert queued.call_args.args[1] == ["42"]
    # The task sends and counts closed DMs.
    from hubra.core.tenant.context import in_club

    from hubra_discord.dm import send_all

    def closed(user_id, body):
        raise DiscordError("closed", status=403)

    with in_club(tenant), patch.object(api, "dm", side_effect=closed):
        assert send_all(conn, ["42"], "T", "x", "Anna") == {
            "sent": 0,
            "closed": 1,
            "failed": 0,
        }
    # Without the notify scope: refused.
    conn.scopes = ["announce"]
    db.session.commit()
    resp = client.post(
        base,
        json={"source": source, "title": "x", "text": "y"},
        headers=H,
    )
    assert resp.status_code == 409


def test_roles_channel_button_shows_the_groups(client, db, tenant, trainer, seeded_db):
    from hubra.core.models.group import Group

    _conn(db, tenant)
    db.session.add(Group(name="Chor", visibility="open"))
    db.session.commit()
    click = {
        "type": 3,
        "guild_id": GUILD,
        "member": {"user": {"id": "42"}},
        "data": {"custom_id": "gpick"},
    }
    answer = _signed(client, click).get_json()
    assert answer["type"] == 4 and "Chor" in answer["data"]["content"]
    assert answer["data"]["flags"] == 64


def test_layout_with_own_names_and_several_new_categories(
    client, db, club_auth, tenant, discord_settings
):
    conn = _conn(db, tenant, scopes=("announce", "channels"), channel_id=None)
    server = LayoutServer()
    base = f"/api/v1/discord/connections/{conn.id}/layout"
    with patch.object(api, "bot", side_effect=server):
        resp = client.post(
            base,
            json={
                "slots": [
                    {"key": "rules", "name": "Hausordnung", "category": "new:Info"},
                    {"key": "announcements", "name": "news", "category": "new:Info"},
                    {"key": "events", "name": "Termine", "category": "new:Anlässe"},
                    {"key": "bot", "category": "903"},
                ]
            },
            headers=H,
        )
        assert resp.status_code == 201, resp.get_json()
        made = resp.get_json()["made"]
        bad = client.post(
            base, json={"slots": [{"key": "log", "category": "12345"}]}, headers=H
        )
    cats = [m for m in made if m["slot"] == "category"]
    assert [c["name"] for c in cats] == ["Info", "Anlässe"]
    by_slot = {m["slot"]: m for m in made}
    assert by_slot["rules"]["name"] == "hausordnung"
    assert by_slot["events"]["name"] == "termine"
    by_id = {c["id"]: c for c in server.channels}
    assert by_id[by_slot["rules"]["id"]]["parent_id"] == cats[0]["id"]
    assert by_id[by_slot["announcements"]["id"]]["parent_id"] == cats[0]["id"]
    assert by_id[by_slot["events"]["id"]]["parent_id"] == cats[1]["id"]
    assert by_id[by_slot["bot"]["id"]]["parent_id"] == "903"
    assert bad.status_code == 409
