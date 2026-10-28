"""Discord webhook payloads and delivery."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

_log = logging.getLogger(__name__)

_HOSTS = {"discord.com", "discordapp.com", "canary.discord.com", "ptb.discord.com"}
COLOR = 0x1F9D57  # the suite's green


class WebhookError(Exception):
    def __init__(self, message: str, status: int | None = None, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        """Rate limits, server errors and network trouble; not a bad or
        deleted webhook."""
        if self.status is None:
            return not str(self).startswith("Not a")
        return self.status == 429 or self.status >= 500


def is_webhook_url(url: str | None) -> bool:
    """Only Discord's own webhook endpoints, so the setting cannot be used to
    make the server call arbitrary hosts."""
    if not url:
        return False
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname in _HOSTS
        and parsed.path.startswith("/api/webhooks/")
    )


def _clip(text: str | None, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def embed(
    title: str,
    description: str | None = None,
    url: str | None = None,
    fields: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "title": _clip(title, 256),
        "color": COLOR,
    }
    if description:
        data["description"] = _clip(description, 2000)
    if url:
        data["url"] = url
    if fields:
        data["fields"] = [
            {"name": _clip(n, 256), "value": _clip(v, 1024), "inline": True}
            for n, v in fields
            if v
        ][:25]
    return data


def payload(content: str | None, embeds: list[dict]) -> dict[str, Any]:
    data: dict[str, Any] = {
        "embeds": embeds,
        # Only the configured mention may ping anyone.
        "allowed_mentions": {"parse": ["everyone", "roles"] if content else []},
    }
    if content:
        data["content"] = _clip(content, 2000)
    return data


def send(url: str, body: dict[str, Any], timeout: float = 10) -> None:
    """POST to the webhook. Raises WebhookError on failure."""
    if not is_webhook_url(url):
        raise WebhookError("Not a Discord webhook URL")
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "hubra"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310
            resp.read()
    except urllib.error.HTTPError as exc:
        retry_after = None
        if exc.code == 429:
            try:
                retry_after = float(json.loads(exc.read() or b"{}").get("retry_after"))
            except (ValueError, TypeError):
                retry_after = None
        raise WebhookError(
            f"Discord answered {exc.code}", exc.code, retry_after
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise WebhookError(f"Discord unreachable: {exc}") from exc
