import { useEffect, useState } from 'react'
import {
  ActionIcon,
  Alert,
  Anchor,
  Button,
  Group,
  MultiSelect,
  Select,
  Stack,
  Switch,
  Text,
  Textarea,
} from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import {
  getConnectionOptions,
  registerPlugin,
  updateConnection,
  useT,
  useToast,
} from '@hubra/plugin-sdk'
import type { Connection, SlotProps } from '@hubra/plugin-sdk'
import translations from './translations.json'

interface Options {
  channels: { id: string; name: string }[]
  roles: { id: string; name: string; assignable: boolean }[]
  sources: { key: string; label: string; kind: string }[]
  missingPermissions?: string[]
  grantUrl?: string
}

interface GroupChannel {
  group_id: number
  channel_id: string
  role_id: string
  name: string
}

interface RoleMapping {
  source: string
  role_id: string
}

type Kind = 'events' | 'posts' | 'polls' | 'reminders'

interface DiscordConfig {
  channel_id: string | null
  channels: Partial<Record<Kind, string>>
  announce_events: boolean
  announce_posts: boolean
  announce_polls: boolean
  announce_reminders: boolean
  members_only_events: boolean
  mention: string
  role_map: RoleMapping[]
  club_profile: boolean
  club_embeds: boolean
  poll_buttons: boolean
  discord_events: boolean
  group_channels: GroupChannel[]
  group_announce: boolean
  welcome_dm: boolean
  welcome_text: string
  self_checkin: boolean
}

const MAX_WELCOME = 1500

const KINDS: {
  kind: Kind
  toggle: 'announce_events' | 'announce_posts' | 'announce_polls' | 'announce_reminders'
  labelKey: string
  label: string
}[] = [
  { kind: 'events', toggle: 'announce_events', labelKey: 'discord.events', label: 'Neue Anlässe' },
  { kind: 'posts', toggle: 'announce_posts', labelKey: 'discord.posts', label: 'Neue Beiträge' },
  { kind: 'polls', toggle: 'announce_polls', labelKey: 'discord.polls', label: 'Neue Umfragen' },
  {
    kind: 'reminders',
    toggle: 'announce_reminders',
    labelKey: 'discord.reminders',
    label: 'Erinnerungen (Anlass steht bevor, Umfrage schliesst bald)',
  },
]

function readConfig(c: Connection): DiscordConfig {
  const raw = c.config as Partial<DiscordConfig>
  return {
    channel_id: raw.channel_id ?? null,
    channels: raw.channels ?? {},
    announce_events: raw.announce_events ?? true,
    announce_posts: raw.announce_posts ?? true,
    announce_polls: raw.announce_polls ?? false,
    announce_reminders: raw.announce_reminders ?? false,
    members_only_events: raw.members_only_events ?? false,
    mention: raw.mention ?? '',
    role_map: raw.role_map ?? [],
    club_profile: raw.club_profile ?? true,
    club_embeds: raw.club_embeds ?? true,
    poll_buttons: raw.poll_buttons ?? true,
    discord_events: raw.discord_events ?? true,
    group_channels: raw.group_channels ?? [],
    group_announce: raw.group_announce ?? true,
    welcome_dm: raw.welcome_dm ?? false,
    welcome_text: raw.welcome_text ?? '',
    self_checkin: raw.self_checkin ?? true,
  }
}

/** In Einstellungen › Verbundene Apps: the Discord connection's settings. */
function DiscordSettings({ connection, onChange }: SlotProps['apps.connection']) {
  const { t } = useT()
  const { notify } = useToast()
  const [config, setConfig] = useState(() => readConfig(connection))
  const [groupIds, setGroupIds] = useState<string[]>(() =>
    readConfig(connection).group_channels.map((g) => String(g.group_id)),
  )
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    const next = readConfig(connection)
    setConfig(next)
    setGroupIds(next.group_channels.map((g) => String(g.group_id)))
  }, [connection])

  const options = useQuery({
    queryKey: ['discord-options', connection.id],
    queryFn: () => getConnectionOptions<Options>(connection.id),
    enabled: connection.provider === 'discord',
  })
  if (connection.provider !== 'discord') return null
  if (options.isError)
    return (
      <Alert color="red" variant="light">
        {(options.error as Error).message}
      </Alert>
    )
  if (!options.data) return null

  const announce = connection.scopes.includes('announce')
  const roles = connection.scopes.includes('roles')
  const commands = connection.scopes.includes('commands')
  const events = connection.scopes.includes('events')
  const channels = connection.scopes.includes('channels')
  const dms = connection.scopes.includes('notify')
  const missing = options.data.missingPermissions ?? []
  const savedGroups = config.group_channels.map((g) => String(g.group_id))
  const groupsChanged =
    groupIds.length !== savedGroups.length || groupIds.some((g, i) => g !== savedGroups[i])
  const groupData = options.data.sources
    .filter((s) => s.kind === 'group')
    .map((s) => ({ value: s.key.slice('group:'.length), label: s.label }))
  const set = (patch: Partial<DiscordConfig>) => setConfig((c) => ({ ...c, ...patch }))
  const channelData = options.data.channels.map((c) => ({ value: c.id, label: `#${c.name}` }))
  const roleName = (id: string) => options.data.roles.find((r) => r.id === id)?.name ?? id

  const save = async () => {
    setBusy(true)
    try {
      const sent: Partial<DiscordConfig> = {
        club_profile: config.club_profile,
        club_embeds: config.club_embeds,
      }
      if (announce) {
        Object.assign(sent, {
          channel_id: config.channel_id,
          channels: config.channels,
          announce_events: config.announce_events,
          announce_posts: config.announce_posts,
          announce_polls: config.announce_polls,
          announce_reminders: config.announce_reminders,
          members_only_events: config.members_only_events,
          mention: config.mention,
        })
      }
      if (roles) sent.role_map = config.role_map.filter((m) => m.source && m.role_id)
      if (commands) {
        sent.poll_buttons = config.poll_buttons
        sent.self_checkin = config.self_checkin
      }
      if (events) sent.discord_events = config.discord_events
      if (channels) {
        sent.group_announce = config.group_announce
        // Only when the list changed: each save checks the channels on Discord.
        if (groupsChanged) (sent as Record<string, unknown>).group_channels = groupIds.map(Number)
      }
      if (dms) {
        sent.welcome_dm = config.welcome_dm
        sent.welcome_text = config.welcome_text
      }
      await updateConnection(connection.id, { config: sent as Record<string, unknown> })
      notify('success', t('discord.saved', 'Gespeichert'))
      onChange()
    } catch (err) {
      notify('error', t('discord.saveFailed', 'Speichern fehlgeschlagen'), (err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Stack gap="sm">
      {missing.length > 0 && (
        <Alert
          color="orange"
          variant="light"
          title={t('discord.missingTitle', 'Dem Bot fehlen Berechtigungen')}
        >
          <Text size="sm">
            {t(
              'discord.missingText',
              'Für die eingeschalteten Bereiche braucht die Hubra-Rolle noch: {{perms}}. Füge den Bot noch einmal zum Server hinzu, Discord ergänzt dann seine Rolle.',
              { perms: missing.join(', ') },
            )}
          </Text>
          {options.data.grantUrl && (
            <Anchor href={options.data.grantUrl} target="_blank" rel="noreferrer" size="sm">
              {t('discord.grant', 'Bot erneut hinzufügen')}
            </Anchor>
          )}
        </Alert>
      )}
      {announce && (
        <>
          <Text fw={600} size="sm">
            {t('discord.announcements', 'Ankündigungen')}
          </Text>
          <Select
            label={t('discord.channel', 'Hauptkanal')}
            description={t(
              'discord.channelHint',
              'Hierhin geht alles, wofür unten kein eigener Kanal gewählt ist.',
            )}
            placeholder={t('discord.pickChannel', 'Kanal wählen')}
            data={channelData}
            value={config.channel_id}
            onChange={(v) => set({ channel_id: v })}
            searchable
            clearable
          />
          {KINDS.map(({ kind, toggle, labelKey, label }) => (
            <Group key={kind} gap="sm" wrap="nowrap" align="center">
              <Switch
                style={{ flex: 1 }}
                label={t(labelKey, label)}
                checked={config[toggle]}
                onChange={(e) =>
                  set({ [toggle]: e.currentTarget.checked } as Partial<DiscordConfig>)
                }
              />
              <Select
                style={{ flex: 1 }}
                aria-label={t('discord.channelFor', 'Kanal für {{what}}', {
                  what: t(labelKey, label),
                })}
                placeholder={t('discord.mainChannel', 'Hauptkanal')}
                data={channelData}
                value={config.channels[kind] ?? null}
                onChange={(v) => {
                  const channels = { ...config.channels }
                  if (v) channels[kind] = v
                  else delete channels[kind]
                  set({ channels })
                }}
                disabled={!config[toggle]}
                searchable
                clearable
              />
            </Group>
          ))}
          <Switch
            label={t('discord.membersOnly', 'Auch Anlässe nur für Mitglieder')}
            description={t(
              'discord.membersOnlyHint',
              'Nur einschalten, wenn den Kanal nur Mitglieder sehen. Beiträge erscheinen nur mit Titel und Kurztext.',
            )}
            checked={config.members_only_events}
            onChange={(e) => set({ members_only_events: e.currentTarget.checked })}
          />
          <Select
            label={t('discord.mention', 'Erwähnen')}
            data={[
              { value: '', label: t('discord.mentionNone', 'Niemanden') },
              { value: 'everyone', label: '@everyone' },
              { value: 'here', label: '@here' },
              ...options.data.roles.map((r) => ({ value: `role:${r.id}`, label: `@${r.name}` })),
            ]}
            value={config.mention}
            onChange={(v) => set({ mention: v ?? '' })}
          />
        </>
      )}

      {roles && (
        <>
          <Text fw={600} size="sm" mt="xs">
            {t('discord.roles', 'Rollen')}
          </Text>
          <Text size="xs" c="dimmed">
            {t(
              'discord.rolesHint',
              'Mitglieder, die ihr Discord-Konto verknüpft haben, bekommen die Rolle, solange sie dazugehören. Der Bot kann nur Rollen unterhalb seiner eigenen vergeben.',
            )}
          </Text>
          {config.role_map.map((m, i) => (
            <Group key={i} gap="xs" wrap="nowrap" align="flex-end">
              <Select
                style={{ flex: 1 }}
                label={i === 0 ? t('discord.source', 'Wer') : undefined}
                data={options.data.sources.map((s) => ({ value: s.key, label: s.label }))}
                value={m.source || null}
                searchable
                onChange={(v) =>
                  set({
                    role_map: config.role_map.map((x, j) =>
                      j === i ? { ...x, source: v ?? '' } : x,
                    ),
                  })
                }
              />
              <Select
                style={{ flex: 1 }}
                label={i === 0 ? t('discord.role', 'Discord-Rolle') : undefined}
                data={options.data.roles.map((r) => ({
                  value: r.id,
                  label: `@${r.name}`,
                  disabled: !r.assignable,
                }))}
                value={m.role_id || null}
                searchable
                onChange={(v) =>
                  set({
                    role_map: config.role_map.map((x, j) =>
                      j === i ? { ...x, role_id: v ?? '' } : x,
                    ),
                  })
                }
              />
              <ActionIcon
                variant="subtle"
                color="red"
                aria-label={t('discord.remove', 'Entfernen')}
                onClick={() => set({ role_map: config.role_map.filter((_, j) => j !== i) })}
              >
                ✕
              </ActionIcon>
            </Group>
          ))}
          {config.role_map.some(
            (m) => m.role_id && !options.data.roles.find((r) => r.id === m.role_id)?.assignable,
          ) && (
            <Alert color="orange" variant="light">
              {t('discord.notAssignable', 'Der Bot kann diese Rolle nicht vergeben: {{roles}}', {
                roles: config.role_map
                  .filter(
                    (m) =>
                      m.role_id && !options.data.roles.find((r) => r.id === m.role_id)?.assignable,
                  )
                  .map((m) => '@' + roleName(m.role_id))
                  .join(', '),
              })}
            </Alert>
          )}
          <Group>
            <Button
              variant="default"
              size="xs"
              onClick={() => set({ role_map: [...config.role_map, { source: '', role_id: '' }] })}
            >
              {t('discord.addRole', 'Rolle zuordnen')}
            </Button>
          </Group>
        </>
      )}

      {events && (
        <>
          <Text fw={600} size="sm" mt="xs">
            {t('discord.serverEvents', 'Discord-Events')}
          </Text>
          <Switch
            label={t('discord.discordEvents', 'Anlässe in der Event-Liste des Servers')}
            description={t(
              'discord.discordEventsHint',
              'Öffentliche Anlässe der nächsten 60 Tage, mit «Interessiert»-Knopf auf Discord. Anlässe nur für Mitglieder nur, wenn oben eingeschaltet. Anlässe einer Gruppe bleiben draussen.',
            )}
            checked={config.discord_events}
            onChange={(e) => set({ discord_events: e.currentTarget.checked })}
          />
        </>
      )}

      {channels && (
        <>
          <Text fw={600} size="sm" mt="xs">
            {t('discord.groupChannels', 'Gruppenkanäle')}
          </Text>
          <MultiSelect
            label={t('discord.groupChannelsPick', 'Private Kanäle für diese Gruppen')}
            description={t(
              'discord.groupChannelsHint',
              'Der Bot legt pro Gruppe einen Kanal und eine Rolle an. Sehen können ihn nur Gruppenmitglieder, die ihr Discord-Konto verknüpft haben. Entfernte Gruppen behalten ihren Kanal, der Bot pflegt ihn nur nicht mehr.',
            )}
            placeholder={t('discord.pickGroups', 'Gruppen wählen')}
            data={groupData}
            value={groupIds}
            onChange={setGroupIds}
            maxValues={25}
            searchable
            clearable
          />
          {config.group_channels.length > 0 && (
            <Text size="xs" c="dimmed">
              {config.group_channels.map((g) => `#${g.name}`).join(', ')}
            </Text>
          )}
          <Switch
            label={t('discord.groupAnnounce', 'Anlässe und Umfragen der Gruppe dort ankündigen')}
            checked={config.group_announce}
            onChange={(e) => set({ group_announce: e.currentTarget.checked })}
          />
        </>
      )}

      {commands && (
        <>
          <Text fw={600} size="sm" mt="xs">
            {t('discord.commands', 'Befehle und Knöpfe')}
          </Text>
          <Switch
            label={t('discord.pollButtons', 'Abstimmen mit Knöpfen unter Umfragen')}
            description={t(
              'discord.pollButtonsHint',
              'Es gelten die Regeln der App: wer abstimmen darf, Stellvertretung, Änderungen. Nur für verknüpfte Mitglieder.',
            )}
            checked={config.poll_buttons}
            onChange={(e) => set({ poll_buttons: e.currentTarget.checked })}
          />
          <Switch
            label={t('discord.selfCheckin', 'Mitglieder melden sich bei /trainings an oder ab')}
            description={t(
              'discord.selfCheckinHint',
              'Ab einer Stunde vor Beginn bis Ende des Tages. Ausgeschaltet zeigt /trainings nur die Termine.',
            )}
            checked={config.self_checkin}
            onChange={(e) => set({ self_checkin: e.currentTarget.checked })}
          />
        </>
      )}

      {dms && (
        <>
          <Text fw={600} size="sm" mt="xs">
            {t('discord.dms', 'Direktnachrichten')}
          </Text>
          <Text size="xs" c="dimmed">
            {t(
              'discord.dmsHint',
              'Benachrichtigungen per Discord wählt jedes Mitglied selbst unter Mein Konto › Benachrichtigungen.',
            )}
          </Text>
          <Switch
            label={t('discord.welcomeDm', 'Neue Leute auf dem Server begrüssen')}
            description={t(
              'discord.welcomeDmHint',
              'Wer dem Server beitritt und noch nicht verknüpft ist, bekommt eine Nachricht mit dem Link zum Verknüpfen. Braucht den Gateway-Dienst der Plattform.',
            )}
            checked={config.welcome_dm}
            onChange={(e) => set({ welcome_dm: e.currentTarget.checked })}
          />
          {config.welcome_dm && (
            <Textarea
              label={t('discord.welcomeText', 'Begrüssungstext')}
              description={t(
                'discord.welcomeTextHint',
                'Leer lassen für den Standardtext. {club} wird zum Vereinsnamen, {link} zum Link fürs Verknüpfen.',
              )}
              autosize
              minRows={3}
              maxLength={MAX_WELCOME}
              placeholder={t(
                'discord.welcomeDefault',
                'Willkommen auf dem Discord-Server von {club}! 👋 Bist du Mitglied? Verknüpfe dein Discord-Konto mit Hubra, dann bekommst du deine Rollen und Kanäle automatisch: {link}',
              )}
              value={config.welcome_text}
              onChange={(e) => set({ welcome_text: e.currentTarget.value })}
            />
          )}
        </>
      )}

      <Text fw={600} size="sm" mt="xs">
        {t('discord.look', 'Aussehen auf diesem Server')}
      </Text>
      <Switch
        label={t('discord.clubProfile', 'Bot erscheint als Verein')}
        description={t(
          'discord.clubProfileHint',
          'Name, Logo und Kurzbeschreibung des Vereins statt «Hubra», nur auf diesem Server. Der Name braucht für die Hubra-Rolle die Berechtigung «Nickname ändern». Ein neues Logo erscheint beim nächsten Speichern hier oder spätestens nach einem Tag.',
        )}
        checked={config.club_profile}
        onChange={(e) => set({ club_profile: e.currentTarget.checked })}
      />
      {announce && (
        <Switch
          label={t('discord.clubEmbeds', 'Ankündigungen in Vereinsfarbe mit Logo')}
          description={t(
            'discord.clubEmbedsHint',
            'Hauptfarbe, Name und Logo des Vereins oben in jeder Ankündigung.',
          )}
          checked={config.club_embeds}
          onChange={(e) => set({ club_embeds: e.currentTarget.checked })}
        />
      )}

      <Group justify="flex-end">
        <Button onClick={save} loading={busy}>
          {t('common.save', 'Speichern')}
        </Button>
      </Group>
    </Stack>
  )
}

registerPlugin({
  id: 'discord',
  slots: [
    {
      slot: 'apps.connection',
      module: 'discord',
      perm: 'apps.apps.manage',
      component: DiscordSettings,
    },
  ],
  translations,
})
