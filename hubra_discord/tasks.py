"""Celery task: deliver one Discord webhook message, with retries."""

from __future__ import annotations

import logging

from hubra.core.tasks import celery_app

from .webhook import WebhookError, send

_log = logging.getLogger(__name__)


@celery_app.task(
    name="hubra_discord.tasks.post_to_discord",
    bind=True,
    max_retries=5,
    acks_late=True,
)
def post_to_discord(self, url: str, body: dict) -> None:
    try:
        send(url, body)
    except WebhookError as exc:
        # Bad URL or a deleted webhook will not get better by retrying.
        if (
            exc.status in (400, 401, 403, 404)
            or exc.status is None
            and "Not a" in str(exc)
        ):
            _log.warning("Discord webhook rejected: %s", exc)
            return
        countdown = exc.retry_after or 2 ** (self.request.retries + 2)
        raise self.retry(exc=exc, countdown=countdown) from exc
