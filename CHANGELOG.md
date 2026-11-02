# Changelog

## 2.3.0

Needs a Hubra with the platform bot (``hubra.core.services.platform_bot``,
the release after 2026-11-02). On an older Hubra everything below stays off.

- Clicks and commands on the platform's own Discord server go to Hubra's
  platform bot: Approve / Reject on club requests, Acknowledge / Silence on
  alerts, `/plattform` and `/wartung` for platform admins.
- Clubs can opt in to Hubra's own notices in the main channel: new Hubra
  versions with their main changes ("Neue Hubra-Versionen ankündigen") and
  planned, started, ended or cancelled maintenance ("Wartungen
  ankündigen"). Both off by default.
- During maintenance, club commands answer that Hubra is being updated.

## 2.2.0

Needs a Hubra with app notification channels, `event.changed` and module
processes (the release after 2026-11-02). On an older Hubra the plugin still
loads; DMs, Discord events and the welcome message stay off.

- Notifications by direct message: members turn Discord on per topic under
  Mein Konto › Benachrichtigungen (new scope "Direktnachrichten").
- The club's events in the server's Events list for the next 60 days, kept
  in step when an event changes or is deleted (scope "Discord-Events").
  Public events only unless the server is for members; group events stay out.
- Private channels per group the club picks (scope "Gruppenkanäle"): the
  bot makes a channel and a role, and the role follows the group. The
  group's events, reminders and polls go to its channel.
- Vote on polls with buttons under the announcement, with the app's rules
  (eligibility, proxies, changing a vote).
- `/rechnungen`, `/trainings` (check in or excuse yourself from an hour
  before the start) and `/profil`, answered only to the person.
- A welcome message for new people on the server who haven't linked yet,
  with an optional own text; linked members get their roles on joining.
  Needs the platform's `discord-gateway` process.
- The settings show which permissions the bot still lacks for the scopes
  and link to adding it again, which updates its role.

## 2.1.0

- The bot appears as the club on the club's server: the club's name as its
  nickname, the club logo as its avatar (PNG, JPEG, GIF or WebP; an SVG logo
  is left out) and a one-line bio. Discord allows this per server, so other
  servers still see Hubra. On by default; off resets to the app's own look.
  Applied when the server is connected, when the club saves its Discord
  settings, and nightly when the club's name or logo changed.
- Announcements show the club's name and logo at the top and use the
  club's main colour (switch "Ankündigungen in Vereinsfarbe mit Logo").
- The add-to-server link also asks for Change Nickname. Servers added
  before 2.1 need it given to the Hubra role for the nickname; the logo and
  bio work without it.

## 2.0.0

The webhook is replaced by the Hubra bot. Needs a Hubra with connected apps
(Einstellungen › Verbundene Apps) and the platform's Discord application set
up under manage › Platform settings › Discord (see Hubra's `docs/discord.md`).

- A club adds the bot to its server under Einstellungen › Verbundene Apps
  and picks what it may do: announcements, roles, sign-ups. An admin
  confirms the server in Hubra before the bot does anything.
- Announcements go to a main channel the club picks, and each kind (new
  events, posts, polls, reminders) can have its own channel instead.
- Reminders from the club's reminder job: "event coming up" (with sign-up
  buttons) and "poll closes soon" (names no one; the app reminds those who
  haven't voted). Off until the club switches them on.
- Members-only content stays limited: members-only events stay
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
