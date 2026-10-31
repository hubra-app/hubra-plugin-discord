# Changelog

## 2.0.0

The webhook is replaced by the Hubra bot. Needs a Hubra with connected apps
(Einstellungen › Verbundene Apps) and the platform's Discord application set
up under manage › Platform settings › Discord (see Hubra's `docs/discord.md`).

- A club adds the bot to its server under Einstellungen › Verbundene Apps
  and picks what it may do: announcements, roles, sign-ups. An admin
  confirms the server in Hubra before the bot does anything.
- Announcements go to a channel the club picks. Members-only events stay
  out unless the club says the channel is members-only; posts show only the
  title and summary; polls for some groups only are never posted.
- Discord roles follow groups, functions, tiers or "all members", for
  members who linked their Discord account on their Hubra account page.
  Only mapped roles are touched, and a nightly sync fixes drift.
- Event posts get Anmelden/Abmelden buttons, and `/events`, `/verbinden`
  and `/hubra status` work on the server.
- Every connection, setting change and bot action is in the connection's
  activity and the club's audit log.
- Upgrading: the old webhook URL setting is gone. Reconnect the server under
  Verbundene Apps.

## 1.1.0

Built for Hubra 0.2 (Mantine 9, plugin host API 2). Needs Hubra 0.2 or newer.

## 1.0.1

- An announcement is sent only after the post, event or poll is saved, so a change that fails afterwards is never announced. A queue outage at that point is logged and does not fail the request.

## 1.0.0

First release as a plugin. Before, Discord was built into Hubra.
