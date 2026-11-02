# Discord

[![Test and release](https://github.com/hubra-app/hubra-plugin-discord/actions/workflows/release.yml/badge.svg?branch=main)](https://github.com/hubra-app/hubra-plugin-discord/actions/workflows/release.yml?query=branch%3Amain) [![License: all rights reserved](https://img.shields.io/badge/license-all%20rights%20reserved-lightgrey)](LICENSE)

A plugin for [Hubra](https://hubra.club). The Hubra bot on a club's Discord server: announcements of new events, posts,
polls and reminders (each kind in its own channel if the club wants), Discord roles that follow the club's groups, functions and tiers,
private channels per group, the club's events in the server's Events list, event sign-ups and poll votes with
buttons, `/rechnungen`, `/trainings` (with check-in) and `/profil`, notifications by direct message for members who
want them, a welcome message for new people on the server, and (opt-in) Hubra's own release and maintenance notices.
On the platform's own server it hands clicks and commands to Hubra's platform bot (club requests, alerts, `/plattform`, `/wartung`).

**Version** 2.3.0 · **Works with** Hubra >=0.2,<1 (with connected apps) · **Plugin id** `discord`

## Install

Platform admins install it from the plugin catalogue in the manager portal
(official Marketplace, <https://plugins.hubra.club/index.json>, signed with
the Hubra project key). Clubs then switch it on under Einstellungen → Module
& Plugins and connect their server under Einstellungen → Verbundene Apps.

The bot is one Discord application for the whole platform; its keys are
platform settings (manage › Platform settings › Discord), stored encrypted. Setup steps are in Hubra's
`docs/discord.md`. The bot asks only for what the club's chosen scopes need
(View Channels, Send Messages, Embed Links; Manage Roles for roles; Manage
Channels and Read Message History for group channels; Create Events for Discord
events; Change Nickname for the club look), never Administrator.

The welcome message needs the platform's gateway process (`flask modules run
discord-gateway`: docker compose profile `discord`, or the k3s Deployment
`discord-gateway`) and the Server Members intent in the Developer Portal.

## Develop

Clone this repository into `plugins/discord` of a Hubra checkout. Its tests
then run with Hubra's (`cd app/backend && pytest ../../plugins/discord`), and
`npm run build:plugin` in `app/frontend` builds its frontend. The workflow
does the same against Hubra's `main` on every push and pull request.

## Releasing

Bump `version` in `plugin.yaml`, add a `## <version>` section to
`CHANGELOG.md`, commit, then publish a GitHub release tagged `v<version>`
(web UI, or `gh release create v<version> --target main --notes ""`). The
workflow tests the plugin and builds its frontend; a separate job in the
`plugin-signing` environment, the only one with the `HUBRA_SIGNING_KEY`
secret, signs it; then `discord-<version>.zip` is attached to the release. Files listed in `.releaseignore` stay out of the zip.

Source: <https://github.com/hubra-app/hubra-plugin-discord>
