"""Discord announcements module."""

from __future__ import annotations

import io
import urllib.error
from unittest.mock import patch

import pytest

from tests.helpers import set_modules
from hubra_discord.webhook import (
    WebhookError,
    is_webhook_url,
    payload,
    send,
)

URL = "https://discord.com/api/webhooks/123/abc"
CONFIG = {
    "webhook_url": URL,
    "announce_events": True,
    "announce_posts": False,
    "announce_polls": True,
    "mention": "@everyone",
}


@pytest.mark.parametrize(
    "url,ok",
    [
        (URL, True),
        ("https://discordapp.com/api/webhooks/1/x", True),
        ("http://discord.com/api/webhooks/1/x", False),
        ("https://evil.example/api/webhooks/1/x", False),
        ("https://discord.com.evil.example/api/webhooks/1/x", False),
        ("https://discord.com/users/1", False),
        ("", False),
        (None, False),
    ],
)
def test_only_discord_webhooks_are_accepted(url, ok):
    assert is_webhook_url(url) is ok


def test_mentions_only_when_configured():
    assert payload(None, [])["allowed_mentions"] == {"parse": []}
    assert payload("@everyone", [])["content"] == "@everyone"


@pytest.fixture
def discord_on(db, seeded_db):
    set_modules(True, "discord")
    with (
        patch("hubra_discord.hooks.get_config", return_value=CONFIG),
        patch("hubra_discord.tasks.post_to_discord.delay") as delay,
    ):
        yield delay


def test_new_event_is_announced(client, auth_headers, discord_on):
    resp = client.post(
        "/api/v1/events/",
        json={
            "title": "Summer party",
            "date": "2026-07-04",
            "time": "18:00-23:00",
            "type": "Social",
            "location": "Garden",
        },
    )
    assert resp.status_code == 201, resp.get_json()
    discord_on.assert_called_once()
    url, body = discord_on.call_args.args
    assert url == URL
    assert body["content"] == "@everyone"
    item = body["embeds"][0]
    assert item["title"].endswith("Summer party")
    assert {"name": "Where", "value": "Garden", "inline": True} in item["fields"]


def test_posts_not_announced_when_switched_off(client, auth_headers, discord_on):
    resp = client.post("/api/v1/posts/", json={"title": "News", "body": "Hello"})
    assert resp.status_code == 201, resp.get_json()
    discord_on.assert_not_called()


def test_poll_is_announced(client, auth_headers, discord_on):
    client.post(
        "/api/v1/polls/",
        json={"question": "Pizza?", "options": [{"label": "Yes"}, {"label": "No"}]},
    )
    body = discord_on.call_args.args[1]
    assert "• Yes" in body["embeds"][0]["description"]


def test_nothing_queued_when_module_off(client, auth_headers, discord_on):
    set_modules(False, "discord")
    client.post(
        "/api/v1/events/",
        json={"title": "x", "date": "2026-07-04", "type": "Social"},
    )
    discord_on.assert_not_called()


def _http_error(code, body=b"{}"):
    return urllib.error.HTTPError(URL, code, "x", {}, io.BytesIO(body))


def test_send_errors_are_classified():
    with patch(
        "urllib.request.urlopen", side_effect=_http_error(429, b'{"retry_after": 1.5}')
    ):
        with pytest.raises(WebhookError) as exc:
            send(URL, {})
    assert exc.value.retryable and exc.value.retry_after == 1.5
    with patch("urllib.request.urlopen", side_effect=_http_error(404)):
        with pytest.raises(WebhookError) as exc:
            send(URL, {})
    assert not exc.value.retryable
    with pytest.raises(WebhookError) as exc:
        send("https://evil.example/x", {})
    assert not exc.value.retryable


def test_test_endpoint_needs_config(client, auth_headers, seeded_db):
    set_modules(True, "discord")
    resp = client.post("/api/v1/discord/test")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "DISCORD_NOT_CONFIGURED"


def test_worker_loads_discord_tasks():
    from hubra.core.modules import registry

    registry.load()
    assert "hubra_discord.tasks" in registry.task_modules()
