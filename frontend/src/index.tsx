import { useEffect, useState } from 'react'
import {
  ActionIcon,
  Alert,
  Anchor,
  Badge,
  Button,
  Checkbox,
  Group,
  MultiSelect,
  Paper,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Tabs,
  Text,
  TextInput,
  Textarea,
  Tooltip,
} from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ConfirmModal,
  apiFetch,
  formatDateTime,
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
  channelProblems?: { channel: string; uses: string[]; missing: string[] }[]
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
  hubra_news: boolean
  maintenance_notices: boolean
  group_roles: boolean
  log_channel_id: string | null
  log_level: 'errors' | 'all'
  nudge_voters: boolean
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
    hubra_news: raw.hubra_news ?? false,
    maintenance_notices: raw.maintenance_notices ?? false,
    group_roles: raw.group_roles ?? false,
    log_channel_id: raw.log_channel_id ?? null,
    log_level: raw.log_level ?? 'errors',
    nudge_voters: raw.nudge_voters ?? false,
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
  const [tab, setTab] = useState<string>('overview')
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
  const channelProblems = options.data.channelProblems ?? []
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
        log_channel_id: config.log_channel_id,
        log_level: config.log_level,
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
          hubra_news: config.hubra_news,
          maintenance_notices: config.maintenance_notices,
          nudge_voters: config.nudge_voters,
        })
      }
      if (roles) {
        sent.role_map = config.role_map.filter((m) => m.source && m.role_id)
        sent.group_roles = config.group_roles
      }
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

  const settingsTab = (value: string, body: React.ReactNode) => (
    <Tabs.Panel value={value} pt="md" key={value}>
      <Stack gap="sm">
        {body}
        {SAVE}
      </Stack>
    </Tabs.Panel>
  )
  const SAVE = (
        <Group justify="flex-end">
          <Button onClick={save} loading={busy}>
            {t('common.save', 'Speichern')}
          </Button>
        </Group>
  )

  return (
    <Stack gap="sm">
      {channelProblems.length > 0 && (
        <Alert
          color="red"
          variant="light"
          title={t('discord.channelProblemsTitle', 'Der Bot kann nicht in alle Kanäle schreiben')}
        >
          <Stack gap={4}>
            {channelProblems.map((p) => (
              <Text size="sm" key={p.channel}>
                {t(
                  'discord.channelProblem',
                  '{{channel}} ({{uses}}): es fehlt {{perms}}. Gib der Hubra-Rolle diese Rechte in den Kanaleinstellungen › Berechtigungen.',
                  { channel: p.channel, uses: p.uses.join(', '), perms: p.missing.join(', ') },
                )}
              </Text>
            ))}
          </Stack>
        </Alert>
      )}
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
      <Tabs value={tab} onChange={(v) => setTab(v ?? 'overview')} keepMounted={false}>
        <Tabs.List style={{ flexWrap: 'wrap' }}>
          <Tabs.Tab value="overview">{t('discord.tab.overview', 'Übersicht')}</Tabs.Tab>
          {announce && <Tabs.Tab value="announce">{t('discord.tab.announce', 'Ankündigungen')}</Tabs.Tab>}
          <Tabs.Tab value="channels">{t('discord.tab.channels', 'Kanäle')}</Tabs.Tab>
          {roles && <Tabs.Tab value="roles">{t('discord.tab.roles', 'Rollen')}</Tabs.Tab>}
          {(commands || events || dms) && (
            <Tabs.Tab value="members">{t('discord.tab.members', 'Mitglieder')}</Tabs.Tab>
          )}
          <Tabs.Tab value="messages">{t('discord.tab.messages', 'Nachrichten')}</Tabs.Tab>
          <Tabs.Tab value="look">{t('discord.tab.look', 'Aussehen & Protokoll')}</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="overview" pt="md">
          <Overview connection={connection} config={config} options={options.data} onOpen={setTab} />
        </Tabs.Panel>
        {announce &&
          settingsTab('announce',
            <>
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
                    <Switch
                      label={t('discord.hubraNews', 'Neue Hubra-Versionen ankündigen')}
                      description={t(
                        'discord.hubraNewsHint',
                        'Im Hauptkanal, mit den wichtigsten Neuerungen.',
                      )}
                      checked={config.hubra_news}
                      onChange={(e) => set({ hubra_news: e.currentTarget.checked })}
                    />
                    <Switch
                      label={t('discord.maintenanceNotices', 'Wartungen ankündigen')}
                      description={t(
                        'discord.maintenanceNoticesHint',
                        'Im Hauptkanal: geplante Wartung, Beginn und Ende. Hubra ist dann kurz nicht erreichbar.',
                      )}
                      checked={config.maintenance_notices}
                      onChange={(e) => set({ maintenance_notices: e.currentTarget.checked })}
                    />
                    <Switch
                      label={t('discord.nudgeVoters', 'Wer noch nicht abgestimmt hat, wird erwähnt')}
                      description={t(
                        'discord.nudgeVotersHint',
                        'Bei Pflicht-Umfragen nennt «Schliesst bald» die verknüpften Mitglieder, die noch fehlen, mit @. Nur wer abstimmen muss, nie was jemand gewählt hat.',
                      )}
                      checked={config.nudge_voters}
                      onChange={(e) => set({ nudge_voters: e.currentTarget.checked })}
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
            </>,
          )}
        <Tabs.Panel value="channels" pt="md">
          <Stack gap="md">
            <ChannelsPanel connection={connection} />
            {channels && (
              <Stack gap="sm">

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
                {SAVE}
              </Stack>
            )}
          </Stack>
        </Tabs.Panel>
        {roles && settingsTab('roles', <>
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
                  <Switch
                    label={t('discord.groupRoles', 'Rolle für jede Gruppe')}
                    description={t(
                      'discord.groupRolesHint',
                      'Der Bot legt für jede offene und sichtbare Gruppe eine Rolle mit ihrem Namen an und hält sie aktuell: umbenennen, löschen, wer drin ist. Private Gruppen bekommen keine, weil auf Discord alle sehen, wer eine Rolle hat. Unten zugeordnete Gruppen behalten ihre Rolle. Ausschalten löscht diese Rollen.',
                    )}
                    checked={config.group_roles}
                    onChange={(e) => set({ group_roles: e.currentTarget.checked })}
                  />
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
        </>)}
        {(commands || events || dms) &&
          settingsTab('members',
            <>
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
            </>,
          )}
        <Tabs.Panel value="messages" pt="md">
          <MessagesPanel connection={connection} />
        </Tabs.Panel>
        {settingsTab('look',
          <>
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

              <Text fw={600} size="sm">
                {t('discord.log', 'Bot-Protokoll')}
              </Text>
              <Text size="xs" c="dimmed">
                {t(
                  'discord.logHint',
                  'Alles, was der Bot tut oder auslässt, steht unten unter «Letzte Aktivität», mit dem Grund. Zusätzlich kann er es in einen Kanal auf eurem Server schreiben.',
                )}
              </Text>
              <Group gap="sm" wrap="wrap" align="flex-end">
                <Select
                  style={{ flex: 1, minWidth: 200 }}
                  label={t('discord.logChannel', 'Protokoll-Kanal')}
                  placeholder={t('discord.logNone', 'Keiner')}
                  data={channelData}
                  value={config.log_channel_id}
                  onChange={(v) => set({ log_channel_id: v })}
                  searchable
                  clearable
                />
                <Select
                  style={{ flex: 1, minWidth: 200 }}
                  label={t('discord.logLevel', 'Was ins Protokoll kommt')}
                  data={[
                    { value: 'errors', label: t('discord.logErrors', 'Nur Fehler') },
                    { value: 'all', label: t('discord.logAll', 'Alles (auch Ausgelassenes)') },
                  ]}
                  value={config.log_level}
                  onChange={(v) => set({ log_level: v === 'all' ? 'all' : 'errors' })}
                  disabled={!config.log_channel_id}
                  allowDeselect={false}
                />
              </Group>
          </>,
        )}
      </Tabs>
    </Stack>
  )
}

// ── Übersicht ────────────────────────────────────────────────────────────────

const SCOPE_TABS: { scope: string; tab: string; key: string; label: string }[] = [
  { scope: 'announce', tab: 'announce', key: 'discord.ov.announce', label: 'Ankündigungen' },
  { scope: 'roles', tab: 'roles', key: 'discord.ov.roles', label: 'Rollen' },
  { scope: 'commands', tab: 'members', key: 'discord.ov.commands', label: 'Befehle und Knöpfe' },
  { scope: 'events', tab: 'members', key: 'discord.ov.events', label: 'Discord-Events' },
  { scope: 'channels', tab: 'channels', key: 'discord.ov.channels', label: 'Kanäle verwalten' },
  { scope: 'notify', tab: 'members', key: 'discord.ov.notify', label: 'Direktnachrichten' },
]

function Overview({
  connection,
  config,
  options,
  onOpen,
}: {
  connection: Connection
  config: DiscordConfig
  options: Options
  onOpen: (tab: string) => void
}) {
  const { t } = useT()
  const channelName = (id: string | null) =>
    id ? `#${options.channels.find((c) => c.id === id)?.name ?? id}` : null
  const on = (label: string, value: boolean) => (
    <Badge key={label} size="sm" variant="light" color={value ? 'green' : 'gray'}>
      {value ? '✓ ' : ''}
      {label}
    </Badge>
  )
  const posted = Object.keys((connection.config as { posted?: object }).posted ?? {}).length
  return (
    <Stack gap="md">
      <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm">
        <Paper withBorder p="sm">
          <Text size="xs" c="dimmed">
            {t('discord.ov.main', 'Hauptkanal')}
          </Text>
          <Text fw={600}>{channelName(config.channel_id) ?? t('discord.ov.none', 'Keiner gewählt')}</Text>
          <Group gap={4} mt={6}>
            {on(t('discord.events', 'Neue Anlässe'), config.announce_events)}
            {on(t('discord.posts', 'Neue Beiträge'), config.announce_posts)}
            {on(t('discord.polls', 'Neue Umfragen'), config.announce_polls)}
            {on(t('discord.ov.reminders', 'Erinnerungen'), config.announce_reminders)}
          </Group>
        </Paper>
        <Paper withBorder p="sm">
          <Text size="xs" c="dimmed">
            {t('discord.ov.live', 'Aktuell gehaltene Nachrichten')}
          </Text>
          <Text fw={600}>{posted}</Text>
          <Anchor size="xs" component="button" type="button" onClick={() => onOpen('messages')}>
            {t('discord.ov.showMessages', 'Nachrichten ansehen')}
          </Anchor>
        </Paper>
        <Paper withBorder p="sm">
          <Text size="xs" c="dimmed">
            {t('discord.ov.roleMaps', 'Rollen')}
          </Text>
          <Text fw={600}>
            {t('discord.ov.roleMapCount', '{{n}} Zuordnungen', { n: config.role_map.length })}
            {config.group_roles ? ` · ${t('discord.ov.groupRoles', 'Gruppenrollen an')}` : ''}
          </Text>
        </Paper>
        <Paper withBorder p="sm">
          <Text size="xs" c="dimmed">
            {t('discord.ov.groupChannels', 'Gruppenkanäle')}
          </Text>
          <Text fw={600}>
            {config.group_channels.length
              ? config.group_channels.map((g) => `#${g.name}`).join(', ')
              : t('discord.ov.none2', 'Keine')}
          </Text>
        </Paper>
      </SimpleGrid>
      <div>
        <Text size="sm" fw={600} mb={4}>
          {t('discord.ov.allowed', 'Was der Bot darf')}
        </Text>
        <Group gap={6}>
          {SCOPE_TABS.map((s) => (
            <Badge
              key={s.scope}
              variant={connection.scopes.includes(s.scope) ? 'light' : 'outline'}
              color={connection.scopes.includes(s.scope) ? 'violet' : 'gray'}
              style={{ cursor: 'pointer' }}
              onClick={() => onOpen(s.tab)}
            >
              {connection.scopes.includes(s.scope) ? '' : '✕ '}
              {t(s.key, s.label)}
            </Badge>
          ))}
        </Group>
        <Text size="xs" c="dimmed" mt={6}>
          {t(
            'discord.ov.scopesHint',
            'Was der Bot darf, legt ihr beim Verbinden fest. Für mehr: Verbindung trennen und neu verbinden, oder den Bot mit dem Link oben erneut hinzufügen.',
          )}
        </Text>
      </div>
      <Text size="sm">
        {t(
          'discord.ov.commandsHint',
          'Auf Discord: /hilfe zeigt, was ihr dort tun könnt (/events, /anlass, /umfrage, /beschluss, /gruppen, /trainings, /rechnungen, /profil).',
        )}
      </Text>
    </Stack>
  )
}

// ── Kanäle ───────────────────────────────────────────────────────────────────

interface ServerChannel {
  id: string
  name: string
  kind: 'text' | 'voice' | 'category' | 'announcement' | 'stage' | 'forum' | 'other'
  parentId: string | null
  topic: string
  private: boolean
  uses: string[]
  madeByHubra: boolean
  botCanPost: boolean
  botCanSee: boolean
  url: string
}

interface ServerChannels {
  channels: ServerChannel[]
  canManage: boolean
  hasScope: boolean
}

const KIND_ICON: Record<string, string> = {
  text: '#',
  announcement: '📣',
  voice: '🔊',
  stage: '🎙️',
  forum: '💬',
  category: '',
  other: '·',
}

function ChannelsPanel({ connection }: { connection: Connection }) {
  const { t } = useT()
  const { notify } = useToast()
  const queryClient = useQueryClient()
  const key = ['discord-server-channels', connection.id]
  const query = useQuery({
    queryKey: key,
    queryFn: () => apiFetch<ServerChannels>(`/discord/connections/${connection.id}/channels`),
  })
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState({ name: '', parentId: '' as string | null, topic: '' })
  const [adding, setAdding] = useState(false)
  const [fresh, setFresh] = useState({
    name: '',
    kind: 'text',
    parentId: null as string | null,
    topic: '',
    private: false,
  })
  const [busy, setBusy] = useState(false)
  const [toDelete, setToDelete] = useState<ServerChannel | null>(null)

  const call = async (what: string, fn: () => Promise<unknown>) => {
    setBusy(true)
    try {
      await fn()
      notify('success', what)
      await queryClient.invalidateQueries({ queryKey: key })
      await queryClient.invalidateQueries({ queryKey: ['discord-options', connection.id] })
      return true
    } catch (err) {
      notify('error', t('discord.ch.failed', 'Das ging nicht'), (err as Error).message)
      return false
    } finally {
      setBusy(false)
    }
  }

  if (query.isLoading) return <Text c="dimmed">{t('discord.loading', 'Lädt …')}</Text>
  if (query.isError)
    return (
      <Alert color="red" variant="light">
        {(query.error as Error).message}
      </Alert>
    )
  const data = query.data as ServerChannels
  const categories = data.channels.filter((c) => c.kind === 'category')
  const catData = [
    { value: '', label: t('discord.ch.noCategory', 'Ohne Kategorie') },
    ...categories.map((c) => ({ value: c.id, label: c.name })),
  ]
  const base = `/discord/connections/${connection.id}/channels`

  return (
    <Stack gap="sm">
      <Group justify="space-between" wrap="wrap">
        <div>
          <Text fw={600} size="sm">
            {t('discord.ch.title', 'Kanäle auf eurem Server')}
          </Text>
          <Text size="xs" c="dimmed">
            {data.canManage
              ? t(
                  'discord.ch.hint',
                  'Anlegen, umbenennen und verschieben geht hier über den Bot. Löschen kann Hubra nur Kanäle, die der Bot selbst angelegt hat.',
                )
              : data.hasScope
                ? t(
                    'discord.ch.noRight',
                    'Der Hubra-Rolle fehlt «Kanäle verwalten». Füge den Bot erneut hinzu, dann geht es hier.',
                  )
                : t(
                    'discord.ch.noScope',
                    'Zum Verwalten braucht die Verbindung den Bereich «Kanäle». Hier siehst du, wofür Hubra welchen Kanal nutzt.',
                  )}
          </Text>
        </div>
        {data.canManage && !adding && (
          <Button size="xs" variant="light" onClick={() => setAdding(true)}>
            {t('discord.ch.add', 'Kanal anlegen')}
          </Button>
        )}
      </Group>

      {adding && (
        <Paper withBorder p="sm">
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="xs">
            <TextInput
              size="xs"
              label={t('discord.ch.name', 'Name')}
              value={fresh.name}
              maxLength={100}
              onChange={(e) => setFresh({ ...fresh, name: e.currentTarget.value })}
            />
            <Select
              size="xs"
              label={t('discord.ch.kind', 'Art')}
              data={[
                { value: 'text', label: t('discord.ch.text', 'Textkanal') },
                { value: 'voice', label: t('discord.ch.voice', 'Sprachkanal') },
                { value: 'category', label: t('discord.ch.category', 'Kategorie') },
              ]}
              value={fresh.kind}
              allowDeselect={false}
              onChange={(v) => setFresh({ ...fresh, kind: v ?? 'text' })}
            />
            {fresh.kind !== 'category' && (
              <Select
                size="xs"
                label={t('discord.ch.parent', 'Kategorie')}
                data={catData}
                value={fresh.parentId ?? ''}
                onChange={(v) => setFresh({ ...fresh, parentId: v || null })}
              />
            )}
            {fresh.kind === 'text' && (
              <TextInput
                size="xs"
                label={t('discord.ch.topic', 'Thema (optional)')}
                value={fresh.topic}
                maxLength={1024}
                onChange={(e) => setFresh({ ...fresh, topic: e.currentTarget.value })}
              />
            )}
          </SimpleGrid>
          <Checkbox
            mt="xs"
            size="xs"
            label={t('discord.ch.private', 'Privat: nur der Bot und Server-Admins sehen ihn (Rollen gibst du auf Discord frei)')}
            checked={fresh.private}
            onChange={(e) => setFresh({ ...fresh, private: e.currentTarget.checked })}
          />
          <Group justify="flex-end" mt="xs" gap="xs">
            <Button size="xs" variant="subtle" onClick={() => setAdding(false)}>
              {t('common.cancel', 'Abbrechen')}
            </Button>
            <Button
              size="xs"
              loading={busy}
              disabled={!fresh.name.trim()}
              onClick={async () => {
                const ok = await call(t('discord.ch.made', 'Kanal angelegt'), () =>
                  apiFetch(base, { method: 'POST', body: JSON.stringify(fresh) }),
                )
                if (ok) {
                  setAdding(false)
                  setFresh({ name: '', kind: 'text', parentId: null, topic: '', private: false })
                }
              }}
            >
              {t('discord.ch.make', 'Anlegen')}
            </Button>
          </Group>
        </Paper>
      )}

      <Paper withBorder p={0}>
        {data.channels.map((c) => {
          const isEditing = editing === c.id
          return (
            <div
              key={c.id}
              style={{
                padding: c.kind === 'category' ? '10px 12px 4px' : '6px 12px 6px 24px',
                borderTop: c.kind === 'category' ? '1px solid var(--mantine-color-default-border)' : undefined,
              }}
            >
              <Group justify="space-between" gap="xs" wrap="nowrap">
                <Group gap={6} wrap="wrap" style={{ minWidth: 0 }}>
                  <Text
                    size={c.kind === 'category' ? 'xs' : 'sm'}
                    fw={c.kind === 'category' ? 700 : 500}
                    tt={c.kind === 'category' ? 'uppercase' : undefined}
                    c={c.kind === 'category' ? 'dimmed' : undefined}
                  >
                    {KIND_ICON[c.kind]} {c.name}
                  </Text>
                  {c.private && <Tooltip label={t('discord.ch.privateTip', 'Privat')}><Text size="xs">🔒</Text></Tooltip>}
                  {c.uses.map((u) => (
                    <Badge key={u} size="xs" variant="light" color="violet">
                      {u}
                    </Badge>
                  ))}
                  {c.uses.length > 0 && !c.botCanPost && c.kind !== 'category' && (
                    <Badge size="xs" variant="light" color="red">
                      {t('discord.ch.cannotPost', 'Bot darf hier nicht schreiben')}
                    </Badge>
                  )}
                  {c.madeByHubra && (
                    <Badge size="xs" variant="outline" color="gray">
                      {t('discord.ch.byHubra', 'von Hubra')}
                    </Badge>
                  )}
                </Group>
                <Group gap={4} wrap="nowrap">
                  <Anchor href={c.url} target="_blank" rel="noreferrer" size="xs">
                    Discord ↗
                  </Anchor>
                  {data.canManage && !isEditing && (
                    <Button
                      size="compact-xs"
                      variant="subtle"
                      onClick={() => {
                        setEditing(c.id)
                        setDraft({ name: c.name, parentId: c.parentId, topic: c.topic })
                      }}
                    >
                      {t('discord.ch.edit', 'Ändern')}
                    </Button>
                  )}
                  {data.canManage && c.madeByHubra && c.uses.every((u) => u.startsWith('Gruppe ')) && (
                    <Button
                      size="compact-xs"
                      variant="subtle"
                      color="red"
                      loading={busy}
                      onClick={() => setToDelete(c)}
                    >
                      {t('discord.ch.delete', 'Löschen')}
                    </Button>
                  )}
                </Group>
              </Group>
              {isEditing && (
                <Group gap="xs" align="flex-end" wrap="wrap" mt={6}>
                  <TextInput
                    size="xs"
                    label={t('discord.ch.name', 'Name')}
                    value={draft.name}
                    maxLength={100}
                    style={{ flex: '1 1 160px' }}
                    onChange={(e) => setDraft({ ...draft, name: e.currentTarget.value })}
                  />
                  {c.kind !== 'category' && (
                    <Select
                      size="xs"
                      label={t('discord.ch.parent', 'Kategorie')}
                      data={catData}
                      value={draft.parentId ?? ''}
                      style={{ flex: '1 1 160px' }}
                      onChange={(v) => setDraft({ ...draft, parentId: v || null })}
                    />
                  )}
                  {(c.kind === 'text' || c.kind === 'announcement') && (
                    <TextInput
                      size="xs"
                      label={t('discord.ch.topic', 'Thema (optional)')}
                      value={draft.topic}
                      maxLength={1024}
                      style={{ flex: '2 1 220px' }}
                      onChange={(e) => setDraft({ ...draft, topic: e.currentTarget.value })}
                    />
                  )}
                  <Button size="xs" variant="subtle" onClick={() => setEditing(null)}>
                    {t('common.cancel', 'Abbrechen')}
                  </Button>
                  <Button
                    size="xs"
                    loading={busy}
                    onClick={async () => {
                      const body: Record<string, unknown> = {}
                      if (draft.name !== c.name) body.name = draft.name
                      if (draft.parentId !== c.parentId && c.kind !== 'category') body.parentId = draft.parentId
                      if (draft.topic !== c.topic) body.topic = draft.topic
                      if (!Object.keys(body).length) return setEditing(null)
                      const ok = await call(t('discord.ch.changed', 'Kanal geändert'), () =>
                        apiFetch(`${base}/${c.id}`, { method: 'PATCH', body: JSON.stringify(body) }),
                      )
                      if (ok) setEditing(null)
                    }}
                  >
                    {t('common.save', 'Speichern')}
                  </Button>
                </Group>
              )}
            </div>
          )
        })}
      </Paper>
      <ConfirmModal
        open={toDelete !== null}
        title={t('discord.ch.confirmTitle', 'Kanal löschen?')}
        body={t('discord.ch.confirmDelete', '«{{name}}» auf Discord löschen? Die Nachrichten darin sind dann weg.', {
          name: toDelete?.name ?? '',
        })}
        onConfirm={() => {
          const target = toDelete
          setToDelete(null)
          if (target)
            void call(t('discord.ch.deleted', 'Kanal gelöscht'), () =>
              apiFetch(`${base}/${target.id}`, { method: 'DELETE' }),
            )
        }}
        onClose={() => setToDelete(null)}
      />
    </Stack>
  )
}

// ── Nachrichten ──────────────────────────────────────────────────────────────

interface LiveMessage {
  key: string
  kind: 'event' | 'poll'
  title: string
  when: string | null
  exists: boolean
  messages: { channelId: string; url: string }[]
}

function MessagesPanel({ connection }: { connection: Connection }) {
  const { t } = useT()
  const { notify } = useToast()
  const queryClient = useQueryClient()
  const key = ['discord-live-messages', connection.id]
  const query = useQuery({
    queryKey: key,
    queryFn: () =>
      apiFetch<{ messages: LiveMessage[] }>(`/discord/connections/${connection.id}/messages`),
  })
  const [busy, setBusy] = useState<string | null>(null)
  const act = async (m: LiveMessage, method: 'POST' | 'DELETE') => {
    setBusy(m.key)
    try {
      const path = `/discord/connections/${connection.id}/messages/${encodeURIComponent(m.key)}`
      await apiFetch(method === 'POST' ? `${path}/refresh` : path, { method })
      notify(
        'success',
        method === 'POST'
          ? t('discord.msg.refreshed', 'Auf Discord aktualisiert')
          : t('discord.msg.stopped', 'Wird nicht mehr aktualisiert'),
      )
      await queryClient.invalidateQueries({ queryKey: key })
    } catch (err) {
      notify('error', t('discord.ch.failed', 'Das ging nicht'), (err as Error).message)
    } finally {
      setBusy(null)
    }
  }
  if (query.isLoading) return <Text c="dimmed">{t('discord.loading', 'Lädt …')}</Text>
  if (query.isError)
    return (
      <Alert color="red" variant="light">
        {(query.error as Error).message}
      </Alert>
    )
  const list = query.data?.messages ?? []
  return (
    <Stack gap="sm">
      <Text size="xs" c="dimmed">
        {t(
          'discord.msg.hint',
          'Diese Anlässe und Umfragen hält der Bot auf Discord aktuell: Plätze, Änderungen, «Abgesagt», das Ergebnis einer Umfrage. Ist ein Anlass vorbei oder eine Umfrage geschlossen, fällt der Eintrag weg.',
        )}
      </Text>
      {list.length === 0 && (
        <Text size="sm" c="dimmed">
          {t('discord.msg.none', 'Gerade keine. Neue Anlässe und Umfragen erscheinen hier, sobald der Bot sie postet.')}
        </Text>
      )}
      {list.map((m) => (
        <Paper key={m.key} withBorder p="sm">
          <Group justify="space-between" wrap="wrap" gap="xs">
            <div style={{ minWidth: 0 }}>
              <Group gap={6}>
                <Badge size="xs" variant="light" color={m.kind === 'event' ? 'violet' : 'blue'}>
                  {m.kind === 'event' ? t('discord.msg.event', 'Anlass') : t('discord.msg.poll', 'Umfrage')}
                </Badge>
                <Text fw={600} size="sm">
                  {m.title}
                </Text>
              </Group>
              <Text size="xs" c="dimmed">
                {m.when ? formatDateTime(m.when) + ' · ' : ''}
                {m.messages.map((msg, i) => (
                  <Anchor key={msg.url} href={msg.url} target="_blank" rel="noreferrer" size="xs" mr={6}>
                    {t('discord.msg.open', 'Nachricht {{n}} ↗', { n: i + 1 })}
                  </Anchor>
                ))}
              </Text>
            </div>
            <Group gap={4}>
              <Button size="compact-xs" variant="default" loading={busy === m.key} onClick={() => act(m, 'POST')}>
                {t('discord.msg.refresh', 'Jetzt aktualisieren')}
              </Button>
              <Button size="compact-xs" variant="subtle" color="gray" disabled={busy === m.key} onClick={() => act(m, 'DELETE')}>
                {t('discord.msg.stop', 'Nicht mehr aktualisieren')}
              </Button>
            </Group>
          </Group>
        </Paper>
      ))}
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
