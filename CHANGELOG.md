# Changelog

## 2.6.0

- **Channel layout.** Kanäle › "Kanäle einrichten" makes #regeln, #ankündigungen,
  #rollen, #anlässe, #anlässe-öffentlich, #bot and #hubra-log in one step, in a
  new or existing category, with the right permissions (public read-only,
  members read-only, members can write, admins only), and sets Hubra up to
  use them. #rollen gets a "Gruppen wählen" button for open groups.
- **Who sees what.** A new tab checks every channel: everyone on the server,
  members only, or only some roles. A public channel needs an approval in
  Hubra ("Öffentlich lassen", audited); "Nur für Mitglieder" fixes it. Also
  flags @everyone with Administrator, Manage Server/Roles/Channels or
  @everyone mentions, and group channels anyone can see. Runs every night;
  new findings go to the bot log.
- **Member role.** Optional "Mitglied" role for every active, linked member;
  members-only channels let it in.
- **Role sync** also follows Hubra roles (e.g. Kassier) and membership types
  (honorary, passive, trial).
- **Group and function channels.** Several channels per group, channels for
  functions (Vorstand, ...), each in a category of your choice and movable
  later. Polls for a function go to its channel.
- **Public events channel.** Public events go to their own channel when one
  is set.
- **Direct message to members.** From Mitglieder: a short message to all
  linked members of a group, function, tier, Hubra role or membership type
  (at most 300, audited).
- **Settings page in tabs.** Übersicht (main channel, what is posted,
  live messages, the bot's rights), Ankündigungen, Kanäle, Rollen,
  Mitglieder, Nachrichten, Aussehen & Protokoll. One save button per tab.
- **Channels from Hubra.** The Kanäle tab lists the server's channels by
  category and shows what Hubra uses each one for, whether it is private and
  whether the bot can post there. Admins can create channels (private by
  default), rename them, change the topic and move them to a category. Hubra
  only deletes channels the bot made itself, and never one still in use.
- **Nachrichten tab.** The events and polls the bot keeps up to date, with
  "Jetzt aktualisieren" and "Nicht mehr aktualisieren".
- **New commands.** /anlass and /umfrage create an event or poll from
  Discord, /beschluss a circular resolution (Ja/Nein/Enthaltung, for a
  function, closes itself once everyone voted), /gruppen joins or leaves
  open groups with buttons, /hilfe lists everything. Creating needs the same
  rights as in Hubra.
- **"Vielleicht"** button on events, and "· N vielleicht" in the places line.
- **Remind non-voters.** Optional: the closing reminder of a required poll
  mentions the linked members who have not voted yet.
- Fixed: saving the settings dropped the list of posted messages, so they
  were no longer kept up to date.

- **Messages stay up to date.** The bot remembers where it posted an event
  or a poll and edits that message later: an event shows the places taken
  ("8 / 20 · 12 frei", "ausgebucht (Warteliste)") after every sign-up or
  cancellation, its new date or place after a change, and "❌ Abgesagt"
  without buttons once it is deleted. Events that are over lose their
  buttons. A poll shows its result once it closes, by hand or at its
  closing time (counts and a bar per option, never who voted what), and the
  channel gets a short "Umfrage beendet" reply. Places update right away
  with Hubra that sends `event.signups_changed` and `poll.closed`; with an
  older Hubra the messages catch up on the next change or within ten
  minutes.
- **Formatting.** All texts in the messages are German like the commands
  (Wann, Wo, Plätze, Schliesst, Von). Times are Discord timestamps, so
  everyone sees them in their own time zone with "in 3 Tagen" next to
  them. Options are numbered, the online link and the registration
  deadline are shown, and a footer says how to sign up or vote.

## 2.5.0

- **Bot log.** Everything the bot does, and everything it leaves out, is
  now in the connection's activity list (Settings › Verbundene Apps ›
  Discord › Details) with the reason: "Event "AGM": members-only event;
  switch on "Auch Anlässe nur für Mitglieder"", ""Neue Umfragen posten" is
  off", "no channel picked", "only for some groups, and none has a
  channel", members-only events left out of the server's Events list,
  direct messages a person doesn't accept, slash commands and their
  crashes. Before, these were skipped without a trace, which looked like
  "the bot posts nothing". Successful posts link to the message on Discord.
- **Log channel** on the club's own server (new "Bot-Protokoll" section):
  pick a channel and whether it gets errors only or everything. A broken
  log channel is noted in Hubra and never loops.
- **Specific permission errors.** Instead of "The bot lacks a permission",
  the log names the post, the channel and the missing permissions, worked
  out from the bot's roles and the channel's own overrides, plus Discord's
  error code and the call, e.g. `Posting events "Summer party" failed in
  #news: the bot lacks Send Messages. Give the Hubra role these permissions
  in the channel's settings … (Discord 50013 Missing Permissions, POST
  /channels/…/messages)`. Role, Events-list and token errors say what to fix
  too. Every entry also goes to the server log (`hubra_discord` logger) with
  the club and Discord server.
- The settings show per channel the bot posts in which permissions it lacks
  there, and **Testen** now posts in every channel in use (main, per kind,
  log) and reports each one.
- A post that still fails after all retries is logged instead of vanishing.

## 2.4.0

- **A role for every group** (Settings › Verbundene Apps › Discord › Rollen:
  "Rolle für jede Gruppe", off by default, needs the roles permission): the
  bot makes a role named after each open or visible group, gives it to the
  group's members who linked Discord, renames it with the group and deletes
  it when the group is deleted, archived or turns private, or when the
  switch is turned off. Private groups get no automatic role, since everyone
  on a server sees who holds a role. Groups mapped to a role by hand or with
  a group channel keep that role; a new group channel takes over the
  automatic role. Up to 50 groups.
- Roles the bot makes (group channels too) no longer copy @everyone's
  permissions; they have none.
- Joining the platform's own server hands over to Hubra's platform roles
  (needs Hubra with `platform_roles`).
- A maintenance window for one club (or some clubs) now also pauses that
  club's Discord commands and buttons, not only platform-wide maintenance.

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
