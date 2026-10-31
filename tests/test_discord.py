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


@pytest.fixture
def discord_settings(app):
    public = SIGNING_KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    app.config.update(
        DISCORD_CLIENT_ID="app1",
        DISCORD_CLIENT_SECRET="secret",
        DISCORD_BOT_TOKEN="bot-token",
        DISCORD_PUBLIC_KEY=public,
    )
    yield
    app.config.update(
        DISCORD_CLIENT_ID="",
        DISCORD_CLIENT_SECRET="",
        DISCORD_BOT_TOKEN="",
        DISCORD_PUBLIC_KEY="",
    )


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
    assert [s.key for s in provider.scopes] == ["announce", "roles", "commands"]
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
    assert (
        bits
        == api.VIEW_CHANNEL | api.SEND_MESSAGES | api.EMBED_LINKS | api.MANAGE_ROLES
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
    ):
        resp = client.post(f"/api/v1/apps/connections/{conn_id}/confirm", headers=H)
    assert resp.status_code == 200
    sync.assert_called_once_with(conn_id)
    register.assert_called_once()
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
    if path.endswith("/members/app1"):
        return {"roles": ["950"]}
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
    connection_id, body = queued.call_args.args
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
    assert "• Yes" in queued.call_args.args[1]["embeds"][0]["description"]


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

    conn = _conn(db, tenant)
    body = payload("", [{"title": "Hi"}])
    with patch.object(api, "bot") as bot:
        send_announcement.run(conn.id, body)
    bot.assert_called_once_with("POST", f"/channels/{CHANNEL}/messages", body)
    event = db.session.query(ConnectedAppEvent).filter_by(action="announced").one()
    assert event.ok and event.detail == "Hi"


def test_announcement_task_records_a_lost_permission(app, db, tenant):
    from hubra_discord.tasks import send_announcement

    conn = _conn(db, tenant)
    with patch.object(api, "bot", side_effect=DiscordError("no", 403)):
        send_announcement.run(conn.id, payload("", [{"title": "Hi"}]))
    db.session.refresh(conn)
    assert "permission" in conn.last_error


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
