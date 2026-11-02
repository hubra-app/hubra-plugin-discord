"""Voting on a poll with buttons under its announcement.

A click votes as in the app (``PollService.vote``): only linked members with
the vote right, only in polls they may vote in (groups, functions), not
after it closed, and not when they gave their vote to a proxy. Whoever holds
proxies gets buttons to cast those votes too. Every answer is only visible
to the person who clicked; nobody else sees who voted for what.
"""

from __future__ import annotations

from hubra.core import apps
from hubra.core.errors import ApiError
from hubra.core.extensions import db

# Discord: 5 buttons per row, 5 rows per message.
MAX_OPTIONS = 25


def _label(text: str) -> str:
    text = (text or "").strip() or "–"
    return text if len(text) <= 80 else text[:79] + "…"


def buttons(poll) -> list[dict] | None:
    """One button per option (None for polls with too many options)."""
    options = sorted(poll.options, key=lambda o: o.id)
    if not options or len(options) > MAX_OPTIONS:
        return None
    rows = []
    for start in range(0, len(options), 5):
        rows.append(
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 1 if len(options) <= 3 else 2,
                        "label": _label(o.label),
                        "custom_id": f"vote:{poll.id}:{start + i}",
                    }
                    for i, o in enumerate(options[start : start + 5])
                ],
            }
        )
    return rows


def handle(conn, member, custom_id: str) -> dict:
    """``vote:<poll>:<option index>[:<for member>]``."""
    from hubra.api.polls.services import ProxyService, PollService

    from .commands import _link_hint, _may, reply

    parts = custom_id.split(":")
    if len(parts) not in (3, 4) or not all(p.isdigit() for p in parts[1:]):
        return reply("Diese Schaltfläche gilt nicht mehr.")
    poll_id, index = int(parts[1]), int(parts[2])
    for_member = int(parts[3]) if len(parts) == 4 else None
    if member is None:
        return reply(_link_hint())
    if not _may(member, "polls.polls.vote"):
        return reply("Du darfst nicht abstimmen.")
    from hubra.core.models.poll import Poll

    poll = db.session.get(Poll, poll_id)
    if poll is None:
        return reply("Diese Umfrage gibt es nicht mehr.")
    options = sorted(poll.options, key=lambda o: o.id)
    if index >= len(options):
        return reply("Diese Antwort gibt es nicht mehr.")
    try:
        PollService.vote(poll, member.id, index, on_behalf_of=for_member)
        db.session.commit()
    except ApiError as exc:
        db.session.rollback()
        return reply(
            {
                "CONFLICT": "Die Umfrage ist geschlossen.",
                "PROXY_GIVEN": "Du hast deine Stimme abgegeben (Vollmacht). Nimm sie in der App zurück, um selbst abzustimmen.",
                "NOT_ELIGIBLE": "Diese Umfrage ist nur für bestimmte Gruppen oder Funktionen.",
                "NOT_PROXY": "Du hast keine Vollmacht für dieses Mitglied.",
                "PROXY_NOT_ALLOWED": "Diese Vollmacht gilt nicht mehr.",
            }.get(exc.code, exc.message)
        )
    choice = options[index].label
    apps.record(conn, "voted", True, f"#{poll.id}")  # never who or what
    db.session.commit()
    if for_member is not None:
        from hubra.core.models.member import Member

        grantor = db.session.get(Member, for_member)
        name = grantor.name if grantor else f"#{for_member}"
        return reply(f"✅ Für {name} abgestimmt: **{choice}**.")

    text = f"✅ Deine Stimme: **{choice}**. Du kannst sie bis zum Schluss ändern."
    held = ProxyService.held(poll.id, member.id)[:5]
    if not held:
        return reply(text)
    answer = reply(
        text + "\nDu hast Vollmachten. Gleich stimmen für:",
    )
    answer["data"]["components"] = [
        {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 2,
                    "label": _label(
                        p.grantor.name if p.grantor else f"#{p.grantor_id}"
                    ),
                    "custom_id": f"vote:{poll.id}:{index}:{p.grantor_id}",
                }
                for p in held
            ],
        }
    ]
    return answer
