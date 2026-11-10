import { useEffect, useState } from 'react'
import {
  ActionIcon,
  Alert,
  Anchor,
  Badge,
  Button,
  Checkbox,
  Group,
  Paper,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Tabs,
  TagsInput,
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
  source?: string
  group_id?: number
  function_id?: number
  channel_id: string
  role_id: string
  name: string
  channel_name?: string
  category_id?: string | null
}

/** One row of the group and function channels editor. */
interface GroupRow {
  source: string
  channelId: string | null
  name: string
  categoryId: string | null
}

function groupRows(list: GroupChannel[]): GroupRow[] {
  return list.map((g) => ({
    source: g.source ?? `group:${g.group_id}`,
    channelId: g.channel_id,
    name: g.channel_name ?? '',
    categoryId: g.category_id ?? null,
  }))
}

interface RoleMapping {
  source: string
  role_id: string
}

type Kind = 'events' | 'posts' | 'polls' | 'reminders' | 'public_events' | 'changelog' | 'onsite'

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
  plugin_news: boolean
  maintenance_notices: boolean
  group_roles: boolean
  log_channel_id: string | null
  log_level: 'errors' | 'all'
  nudge_voters: boolean
  member_role: boolean
  member_role_name: string
  training_posts: boolean
  stats_channel: boolean
  stats_name: string
  weekly_events: boolean
  weekly_stats: boolean
  mod_channel_id: string | null
  reports: boolean
  guest_queue: boolean
  guest_role_name: string
  kick_leavers: boolean
  event_feedback: boolean
  onsite_today: boolean
}

const MAX_WELCOME = 1500

const KINDS: {
  kind: Kind
  toggle:
    | 'announce_events'
    | 'announce_posts'
    | 'announce_polls'
    | 'announce_reminders'
    | 'hubra_news'
    | 'onsite_today'
  labelKey: string
  label: string
}[] = [
  { kind: 'events', toggle: 'announce_events', labelKey: 'discord.events', label: 'Neue Anlässe' },
  {
    kind: 'public_events',
    toggle: 'announce_events',
    labelKey: 'discord.publicEvents',
    label: 'Öffentliche Anlässe (eigener Kanal, sonst wie Anlässe)',
  },
  { kind: 'posts', toggle: 'announce_posts', labelKey: 'discord.posts', label: 'Neue Beiträge' },
  { kind: 'polls', toggle: 'announce_polls', labelKey: 'discord.polls', label: 'Neue Umfragen' },
  {
    kind: 'reminders',
    toggle: 'announce_reminders',
    labelKey: 'discord.reminders',
    label: 'Erinnerungen (Anlass steht bevor, Umfrage schliesst bald)',
  },
  {
    kind: 'changelog',
    toggle: 'hubra_news',
    labelKey: 'discord.changelog',
    label: 'Changelog: neue Hubra-Versionen (z. B. Ankündigungs- oder eigener Changelog-Kanal)',
  },
  {
    kind: 'onsite',
    toggle: 'onsite_today',
    labelKey: 'discord.onsite',
    label: 'Heute vor Ort (Buchungen: wer heute in welchem Raum ist; nennt Namen, nur in einem Kanal nur für Mitglieder)',
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
    plugin_news: raw.plugin_news ?? false,
    maintenance_notices: raw.maintenance_notices ?? false,
    group_roles: raw.group_roles ?? false,
    log_channel_id: raw.log_channel_id ?? null,
    log_level: raw.log_level ?? 'errors',
    nudge_voters: raw.nudge_voters ?? false,
    member_role: raw.member_role ?? false,
    member_role_name: raw.member_role_name ?? '',
    training_posts: raw.training_posts ?? true,
    stats_channel: raw.stats_channel ?? false,
    stats_name: raw.stats_name ?? '',
    weekly_events: raw.weekly_events ?? false,
    weekly_stats: raw.weekly_stats ?? false,
    mod_channel_id: raw.mod_channel_id ?? null,
    reports: raw.reports ?? true,
    guest_queue: raw.guest_queue ?? false,
    guest_role_name: raw.guest_role_name ?? '',
    kick_leavers: raw.kick_leavers ?? false,
    event_feedback: raw.event_feedback ?? false,
    onsite_today: raw.onsite_today ?? false,
  }
}

/** In Einstellungen › Verbundene Apps: the Discord connection's settings. */
function DiscordSettings({ connection, onChange }: SlotProps['apps.connection']) {
  const { t } = useT()
  const { notify } = useToast()
  const [config, setConfig] = useState(() => readConfig(connection))
  const [rows, setRows] = useState<GroupRow[]>(() => groupRows(readConfig(connection).group_channels))
  const [busy, setBusy] = useState(false)
  const [tab, setTab] = useState<string>('overview')
  useEffect(() => {
    const next = readConfig(connection)
    setConfig(next)
    setRows(groupRows(next.group_channels))
  }, [connection])

  const options = useQuery({
    queryKey: ['discord-options', connection.id],
    queryFn: () => getConnectionOptions<Options>(connection.id),
    enabled: connection.provider === 'discord',
  })
  const server = useQuery({
    queryKey: ['discord-server-channels', connection.id],
    queryFn: () => apiFetch<ServerChannels>(`/discord/connections/${connection.id}/channels`),
    enabled: connection.provider === 'discord' && connection.scopes.includes('channels'),
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
  const moderate = connection.scopes.includes('moderate')
  const missing = options.data.missingPermissions ?? []
  const channelProblems = options.data.channelProblems ?? []
  const groupsChanged = JSON.stringify(rows) !== JSON.stringify(groupRows(config.group_channels))
  const groupData = options.data.sources
    .filter((s) => s.kind === 'group' || s.kind === 'function')
    .map((s) => ({
      value: s.key,
      label:
        s.kind === 'function'
          ? t('discord.functionLabel', '{{name}} (Funktion)', { name: s.label })
          : s.label,
    }))
  const sourceName = (key: string) => groupData.find((g) => g.value === key)?.label ?? key
  const categoryData = [
    { value: '', label: t('discord.ch.noCategory', 'Ohne Kategorie') },
    ...(server.data?.channels ?? [])
      .filter((c) => c.kind === 'category')
      .map((c) => ({ value: c.id, label: c.name })),
  ]
  const setRow = (i: number, patch: Partial<GroupRow>) =>
    setRows((list) => list.map((r, j) => (j === i ? { ...r, ...patch } : r)))
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
        weekly_stats: config.weekly_stats,
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
          plugin_news: config.plugin_news,
          maintenance_notices: config.maintenance_notices,
          nudge_voters: config.nudge_voters,
          weekly_events: config.weekly_events,
        })
      }
      if (roles) {
        sent.role_map = config.role_map.filter((m) => m.source && m.role_id)
        sent.group_roles = config.group_roles
        sent.member_role = config.member_role
        sent.member_role_name = config.member_role_name
      }
      if (commands) {
        sent.poll_buttons = config.poll_buttons
        sent.self_checkin = config.self_checkin
        sent.event_feedback = config.event_feedback
      }
      if (events) sent.discord_events = config.discord_events
      if (channels) {
        sent.group_announce = config.group_announce
        sent.training_posts = config.training_posts
        sent.stats_channel = config.stats_channel
        sent.stats_name = config.stats_name
        // Only when the list changed: each save checks the channels on Discord.
        if (groupsChanged)
          (sent as Record<string, unknown>).group_channels = rows
            .filter((r) => r.source)
            .map((r) => ({
              source: r.source,
              channelId: r.channelId,
              name: r.name,
              categoryId: r.categoryId,
            }))
      }
      if (moderate) {
        Object.assign(sent, {
          mod_channel_id: config.mod_channel_id,
          reports: config.reports,
          guest_queue: config.guest_queue,
          guest_role_name: config.guest_role_name,
          kick_leavers: config.kick_leavers,
        })
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
          <Tabs.Tab value="audit">{t('discord.tab.audit', 'Wer sieht was')}</Tabs.Tab>
          {roles && <Tabs.Tab value="roles">{t('discord.tab.roles', 'Rollen')}</Tabs.Tab>}
          {(commands || events || dms) && (
            <Tabs.Tab value="members">{t('discord.tab.members', 'Mitglieder')}</Tabs.Tab>
          )}
          {moderate && <Tabs.Tab value="moderate">{t('discord.tab.moderate', 'Moderation')}</Tabs.Tab>}
          <Tabs.Tab value="numbers">{t('discord.tab.numbers', 'Zahlen & Rückmeldungen')}</Tabs.Tab>
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
                          disabled={!config[toggle] && !(kind === 'changelog' && config.plugin_news)}
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
                      label={t('discord.pluginNews', 'Changelog: Plugin-Updates')}
                      description={t(
                        'discord.pluginNewsHint',
                        'Neue Versionen der Plugins, die der Verein nutzt, mit ihrem Changelog. Im selben Kanal wie die Hubra-Versionen.',
                      )}
                      checked={config.plugin_news}
                      onChange={(e) => set({ plugin_news: e.currentTarget.checked })}
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
                    <Switch
                      label={t('discord.weeklyEvents', 'Montags die Anlässe der Woche')}
                      description={t(
                        'discord.weeklyEventsHint',
                        'Ab 7 Uhr eine Übersicht der nächsten sieben Tage im Kanal für Anlässe, mit Links zum Anmelden. Trainings stehen nicht drin.',
                      )}
                      checked={config.weekly_events}
                      onChange={(e) => set({ weekly_events: e.currentTarget.checked })}
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
            {channels && <LayoutCard connection={connection} />}
            <ChannelsPanel connection={connection} />
            {channels && (
              <Stack gap="sm">

                    <Text fw={600} size="sm" mt="xs">
                      {t('discord.groupChannels', 'Gruppenkanäle')}
                    </Text>
                    <Text size="xs" c="dimmed">
                      {t(
                        'discord.groupChannelsHint2',
                        'Private Kanäle für Gruppen und Funktionen (Vorstand, Trainer, …), auch mehrere pro Gruppe. Der Bot legt pro Gruppe oder Funktion eine Rolle an; sehen können die Kanäle nur, wer dazugehört und Discord verknüpft hat. Entfernte Kanäle bleiben auf Discord, der Bot pflegt sie nur nicht mehr.',
                      )}
                    </Text>
                    {rows.map((r, i) => (
                      <Group key={r.channelId ?? `new-${i}`} gap="xs" wrap="wrap" align="flex-end">
                        <Select
                          style={{ flex: '2 1 180px' }}
                          size="xs"
                          label={i === 0 ? t('discord.groupOrFunction', 'Gruppe oder Funktion') : undefined}
                          data={groupData}
                          value={r.source || null}
                          disabled={!!r.channelId}
                          searchable
                          onChange={(v) => setRow(i, { source: v ?? '' })}
                        />
                        <TextInput
                          style={{ flex: '2 1 160px' }}
                          size="xs"
                          label={i === 0 ? t('discord.channelName', 'Kanalname') : undefined}
                          placeholder={r.source ? sourceName(r.source).toLowerCase() : ''}
                          value={r.channelId ? `#${r.name}` : r.name}
                          disabled={!!r.channelId}
                          maxLength={100}
                          onChange={(e) => setRow(i, { name: e.currentTarget.value })}
                        />
                        <Select
                          style={{ flex: '1 1 140px' }}
                          size="xs"
                          label={i === 0 ? t('discord.ch.parent', 'Kategorie') : undefined}
                          data={categoryData}
                          value={r.categoryId ?? ''}
                          onChange={(v) => setRow(i, { categoryId: v || null })}
                        />
                        <ActionIcon
                          variant="subtle"
                          color="red"
                          mb={4}
                          aria-label={t('discord.remove', 'Entfernen')}
                          onClick={() => setRows(rows.filter((_, j) => j !== i))}
                        >
                          ✕
                        </ActionIcon>
                      </Group>
                    ))}
                    <Group>
                      <Button
                        variant="default"
                        size="xs"
                        disabled={rows.length >= 40}
                        onClick={() =>
                          setRows([...rows, { source: '', channelId: null, name: '', categoryId: null }])
                        }
                      >
                        {t('discord.addGroupChannel', 'Kanal hinzufügen')}
                      </Button>
                    </Group>
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
        <Tabs.Panel value="audit" pt="md">
          <AuditPanel connection={connection} canFix={channels} />
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
                  <Group gap="sm" align="flex-end" wrap="wrap">
                    <Switch
                      style={{ flex: '2 1 260px' }}
                      label={t('discord.memberRole', 'Rolle für alle Mitglieder')}
                      description={t(
                        'discord.memberRoleHint',
                        'Alle aktiven, verknüpften Mitglieder bekommen sie, Ausgetretene und Gesperrte verlieren sie. Kanäle «nur für Mitglieder» geben diese Rolle frei.',
                      )}
                      checked={config.member_role}
                      onChange={(e) => set({ member_role: e.currentTarget.checked })}
                    />
                    {config.member_role && (
                      <TextInput
                        style={{ flex: '1 1 160px' }}
                        size="xs"
                        label={t('discord.memberRoleName', 'Name der Rolle')}
                        placeholder="Mitglied"
                        maxLength={100}
                        value={config.member_role_name}
                        onChange={(e) => set({ member_role_name: e.currentTarget.value })}
                      />
                    )}
                  </Group>
                  <Text size="xs" c="dimmed">
                    {t(
                      'discord.roleSourcesHint',
                      'Zuordnen geht nach Gruppe, Funktion, Beitragskategorie, Hubra-Rolle (z. B. Kassier) oder Mitgliedsart (Ehren-, Passiv-, Probemitglieder).',
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
                    {channels && (
                      <Switch
                        label={t('discord.trainingPosts', 'Trainingskarte im Gruppenkanal')}
                        description={t(
                          'discord.trainingPostsHint',
                          'Statt der Erinnerung vor einem Training: eine Karte im Kanal der Gruppe mit «Bin da», «Kann nicht» und wie viele kommen. Die Zahl geht mit jedem Klick nach.',
                        )}
                        checked={config.training_posts}
                        onChange={(e) => set({ training_posts: e.currentTarget.checked })}
                      />
                    )}
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
                    <DmCard connection={connection} sources={options.data.sources} />
                  </>
                )}
            </>,
          )}
        {moderate &&
          settingsTab('moderate',
            <>
              <Text size="xs" c="dimmed">
                {t(
                  'discord.modHint',
                  'Meldungen und neue Leute gehen als Karte in diesen Kanal. Er sollte nur für Admins sein (wie #hubra-log). Die Knöpfe darauf prüfen die Rechte auf Discord: Nachrichten verwalten, Rollen verwalten, Mitglieder kicken.',
                )}
              </Text>
              <Select
                label={t('discord.modChannel', 'Moderationskanal')}
                placeholder={t('discord.modChannelLog', 'Der Protokoll-Kanal')}
                data={channelData}
                value={config.mod_channel_id}
                onChange={(v) => set({ mod_channel_id: v })}
                searchable
                clearable
              />
              <Switch
                label={t('discord.reports', 'Nachrichten melden')}
                description={t(
                  'discord.reportsHint',
                  'Rechtsklick auf eine Nachricht › Apps › «Melden». Wer meldet, schreibt kurz warum; die Admins bekommen die Nachricht mit «Löschen» und «Erledigt».',
                )}
                checked={config.reports}
                onChange={(e) => set({ reports: e.currentTarget.checked })}
                disabled={!commands}
              />
              <Group gap="sm" align="flex-end" wrap="wrap">
                <Switch
                  style={{ flex: '2 1 260px' }}
                  label={t('discord.guestQueue', 'Neue Leute prüfen')}
                  description={t(
                    'discord.guestQueueHint',
                    'Wer beitritt und kein verknüpftes Mitglied ist, erscheint als Karte: «Als Gast zulassen» gibt die Gast-Rolle, «Entfernen» wirft die Person raus. Braucht den Gateway-Dienst der Plattform.',
                  )}
                  checked={config.guest_queue}
                  onChange={(e) => set({ guest_queue: e.currentTarget.checked })}
                  disabled={!commands}
                />
                {config.guest_queue && (
                  <TextInput
                    style={{ flex: '1 1 160px' }}
                    size="xs"
                    label={t('discord.guestRoleName', 'Name der Gast-Rolle')}
                    placeholder="Gast"
                    maxLength={100}
                    value={config.guest_role_name}
                    onChange={(e) => set({ guest_role_name: e.currentTarget.value })}
                  />
                )}
              </Group>
              {!commands && (
                <Text size="xs" c="orange">
                  {t('discord.modNeedsCommands', 'Melden und neue Leute brauchen auch den Bereich «Befehle».')}
                </Text>
              )}
              <Switch
                label={t('discord.kickLeavers', 'Ausgetretene vom Server entfernen')}
                description={t(
                  'discord.kickLeaversHint',
                  'Jede Nacht: wer nicht mehr Mitglied ist (archiviert oder gelöscht) und Discord verknüpft hatte, wird vom Server entfernt. Gäste, die nie Mitglied waren, bleiben. Wer einmal entfernt wurde, wird nicht nochmals entfernt.',
                )}
                checked={config.kick_leavers}
                onChange={(e) => set({ kick_leavers: e.currentTarget.checked })}
              />
              <LeaversCard connection={connection} />
            </>,
          )}
        {settingsTab('numbers',
          <>
            {channels && (
              <Group gap="sm" align="flex-end" wrap="wrap">
                <Switch
                  style={{ flex: '2 1 260px' }}
                  label={t('discord.statsChannel', 'Mitgliederzahl als Kanal')}
                  description={t(
                    'discord.statsChannelHint',
                    'Ein Sprachkanal zuoberst, dessen Name die Zahl der Mitglieder zeigt. Alle sehen ihn, niemand kann beitreten. Jede Nacht aktualisiert.',
                  )}
                  checked={config.stats_channel}
                  onChange={(e) => set({ stats_channel: e.currentTarget.checked })}
                />
                {config.stats_channel && (
                  <TextInput
                    style={{ flex: '1 1 200px' }}
                    size="xs"
                    label={t('discord.statsName', 'Name ({n} = Zahl)')}
                    placeholder="👥 Mitglieder: {n}"
                    maxLength={90}
                    value={config.stats_name}
                    error={
                      config.stats_name && !config.stats_name.includes('{n}')
                        ? t('discord.statsNameN', 'Mit {n} für die Zahl')
                        : undefined
                    }
                    onChange={(e) => set({ stats_name: e.currentTarget.value })}
                  />
                )}
              </Group>
            )}
            <Switch
              label={t('discord.weeklyStats', 'Montags die Zahlen der Woche für die Admins')}
              description={t(
                'discord.weeklyStatsHint',
                'Im Moderations- oder Protokoll-Kanal: Mitglieder, neu und ausgetreten, mit Discord verknüpft, Anlässe und Trainings.',
              )}
              checked={config.weekly_stats}
              onChange={(e) => set({ weekly_stats: e.currentTarget.checked })}
            />
            <StatsCard connection={connection} />
            {commands && (
              <Switch
                label={t('discord.eventFeedback', 'Nach Anlässen fragen, wie es war')}
                description={t(
                  'discord.eventFeedbackHint',
                  'Ein paar Stunden nach dem Ende: 1 bis 5 und ein Kommentar, dort wo der Anlass angekündigt wurde. Nur wer angemeldet war oder eingecheckt hat. Anonym. Trainings ausgenommen.',
                )}
                checked={config.event_feedback}
                onChange={(e) => set({ event_feedback: e.currentTarget.checked })}
              />
            )}
            <FeedbackCard connection={connection} />
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
                  {data.canManage && c.madeByHubra && c.uses.every((u) => u.startsWith('Gruppe ') || u.startsWith('Funktion ')) && (
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

// ── Kanäle einrichten ────────────────────────────────────────────────────────

interface LayoutState {
  slots: { key: string; name: string; access: string; channelId: string | null; channelName: string | null }[]
  categories: { id: string; name: string }[]
  hasScope: boolean
}

const SLOT_TEXT: Record<string, { key: string; label: string }> = {
  rules: { key: 'discord.lay.rules', label: 'Regeln: alle lesen, niemand schreibt' },
  announcements: { key: 'discord.lay.announcements', label: 'Ankündigungen: Mitglieder lesen, wird Hauptkanal' },
  roles: { key: 'discord.lay.roles', label: 'Rollen: Knopf «Gruppen wählen» für offene Gruppen' },
  events: { key: 'discord.lay.events', label: 'Anlässe intern: Mitglieder lesen und melden sich an' },
  public_events: { key: 'discord.lay.publicEvents', label: 'Anlässe öffentlich: alle sehen öffentliche Anlässe' },
  bot: { key: 'discord.lay.bot', label: 'Bot: Mitglieder schreiben Befehle (/hilfe)' },
  log: { key: 'discord.lay.log', label: 'Bot-Protokoll: nur Admins' },
}

const ACCESS_COLOR: Record<string, string> = {
  public_read: 'orange',
  members_read: 'violet',
  members_write: 'violet',
  private: 'gray',
}

const DEFAULT_CATEGORY: Record<string, string> = {
  rules: 'new:Info',
  announcements: 'new:Info',
  roles: 'new:Info',
  events: 'new:Anlässe',
  public_events: 'new:Anlässe',
  bot: 'new:Info',
  log: '',
}

interface SlotDraft {
  on: boolean
  name: string
  category: string
}

function LayoutCard({ connection }: { connection: Connection }) {
  const { t } = useT()
  const { notify } = useToast()
  const queryClient = useQueryClient()
  const key = ['discord-layout', connection.id]
  const [open, setOpen] = useState(false)
  const query = useQuery({
    queryKey: key,
    queryFn: () => apiFetch<LayoutState>(`/discord/connections/${connection.id}/layout`),
    enabled: open,
  })
  const [drafts, setDrafts] = useState<Record<string, SlotDraft>>({})
  const [newCats, setNewCats] = useState<string[]>(['Info', 'Anlässe'])
  const [busy, setBusy] = useState(false)
  const access = (a: string) =>
    a === 'public_read'
      ? t('discord.lay.public', 'öffentlich')
      : a === 'private'
        ? t('discord.lay.private', 'privat')
        : t('discord.lay.members', 'nur Mitglieder')

  if (!open)
    return (
      <Paper withBorder p="sm">
        <Group justify="space-between" wrap="wrap" gap="xs">
          <div>
            <Text fw={600} size="sm">
              {t('discord.lay.title', 'Kanäle einrichten')}
            </Text>
            <Text size="xs" c="dimmed">
              {t(
                'discord.lay.teaser',
                'Regeln, Ankündigungen, Rollen, Anlässe (intern und öffentlich), Bot und Protokoll in einem Schritt, mit den richtigen Berechtigungen.',
              )}
            </Text>
          </div>
          <Button size="xs" variant="light" onClick={() => setOpen(true)}>
            {t('discord.lay.open', 'Einrichten')}
          </Button>
        </Group>
      </Paper>
    )
  const data = query.data
  const draft = (slot: LayoutState['slots'][number]): SlotDraft =>
    drafts[slot.key] ?? {
      on: ['rules', 'announcements', 'roles', 'events', 'bot'].includes(slot.key),
      name: slot.name,
      category: DEFAULT_CATEGORY[slot.key] ?? '',
    }
  const setDraft = (slot: LayoutState['slots'][number], patch: Partial<SlotDraft>) =>
    setDrafts((d) => ({ ...d, [slot.key]: { ...draft(slot), ...patch } }))
  const catData = [
    { value: '', label: t('discord.ch.noCategory', 'Ohne Kategorie') },
    ...newCats.map((n) => ({ value: `new:${n}`, label: t('discord.lay.newCat', '{{name}} (neu)', { name: n }) })),
    ...(data?.categories ?? []).map((c) => ({ value: c.id, label: c.name })),
  ]
  const todo = (data?.slots ?? []).filter((s) => !s.channelId && draft(s).on)
  const run = async () => {
    setBusy(true)
    try {
      const slots = todo.map((s) => {
        const d = draft(s)
        const category = d.category.startsWith('new:') && !newCats.includes(d.category.slice(4)) ? '' : d.category
        return { key: s.key, name: d.name, category }
      })
      const result = await apiFetch<{ made: { name: string; slot: string }[] }>(
        `/discord/connections/${connection.id}/layout`,
        { method: 'POST', body: JSON.stringify({ slots }) },
      )
      const channels = result.made.filter((m) => m.slot !== 'category')
      notify(
        'success',
        channels.length
          ? t('discord.lay.done', 'Angelegt: {{names}}', { names: channels.map((m) => '#' + m.name).join(', ') })
          : t('discord.lay.nothing', 'Alles schon da'),
      )
      for (const k of [key, ['discord-server-channels', connection.id], ['discord-options', connection.id], ['discord-audit', connection.id]])
        await queryClient.invalidateQueries({ queryKey: k })
    } catch (err) {
      notify('error', t('discord.ch.failed', 'Das ging nicht'), (err as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Paper withBorder p="sm">
      <Stack gap="xs">
        <Text fw={600} size="sm">
          {t('discord.lay.title', 'Kanäle einrichten')}
        </Text>
        <Text size="xs" c="dimmed">
          {t(
            'discord.lay.hint2',
            'Der Bot legt die gewählten Kanäle an und richtet Hubra darauf ein. Name und Kategorie wählst du pro Kanal; neue Kategorien legst du oben an. «Nur Mitglieder» braucht die Mitglied-Rolle; sie wird dafür eingeschaltet. Öffentliche Kanäle gelten damit als freigegeben.',
          )}
        </Text>
        {!data && <Text c="dimmed" size="sm">{t('discord.loading', 'Lädt …')}</Text>}
        {data && (
          <TagsInput
            size="xs"
            label={t('discord.lay.newCategories', 'Neue Kategorien')}
            description={t('discord.lay.newCategoriesHint', 'Werden nur angelegt, wenn ein Kanal sie nutzt.')}
            value={newCats}
            onChange={(v) => setNewCats(v.map((x) => x.trim().slice(0, 100)).filter(Boolean))}
            maxTags={10}
          />
        )}
        {data &&
          data.slots.map((slot) => {
            const d = draft(slot)
            return (
              <Group key={slot.key} gap="xs" wrap="wrap" align="center">
                <Checkbox
                  size="xs"
                  style={{ flex: '0 0 auto' }}
                  disabled={!!slot.channelId}
                  checked={!!slot.channelId || d.on}
                  onChange={(e) => setDraft(slot, { on: e.currentTarget.checked })}
                  aria-label={slot.name}
                />
                <div style={{ flex: '3 1 220px', minWidth: 0 }}>
                  <Text size="sm">
                    {t(SLOT_TEXT[slot.key]?.key ?? slot.key, SLOT_TEXT[slot.key]?.label ?? slot.key)}
                  </Text>
                  <Group gap={4}>
                    <Badge size="xs" variant="light" color={ACCESS_COLOR[slot.access] ?? 'gray'}>
                      {access(slot.access)}
                    </Badge>
                    {slot.channelId && (
                      <Badge size="xs" variant="outline" color="green">
                        {t('discord.lay.thereAs', 'schon da: #{{name}}', { name: slot.channelName ?? '' })}
                      </Badge>
                    )}
                  </Group>
                </div>
                {!slot.channelId && (
                  <>
                    <TextInput
                      size="xs"
                      style={{ flex: '2 1 150px' }}
                      aria-label={t('discord.channelName', 'Kanalname')}
                      leftSection="#"
                      value={d.name}
                      maxLength={100}
                      disabled={!d.on}
                      onChange={(e) => setDraft(slot, { name: e.currentTarget.value })}
                    />
                    <Select
                      size="xs"
                      style={{ flex: '2 1 150px' }}
                      aria-label={t('discord.ch.parent', 'Kategorie')}
                      data={catData}
                      value={catData.some((c) => c.value === d.category) ? d.category : ''}
                      allowDeselect={false}
                      disabled={!d.on}
                      onChange={(v) => setDraft(slot, { category: v ?? '' })}
                    />
                  </>
                )}
              </Group>
            )
          })}
        {data && (
          <Group justify="flex-end" gap="xs">
            <Button size="xs" variant="subtle" onClick={() => setOpen(false)}>
              {t('common.cancel', 'Abbrechen')}
            </Button>
            <Button
              size="xs"
              loading={busy}
              disabled={!data.hasScope || todo.length === 0 || todo.some((s) => !draft(s).name.trim())}
              onClick={run}
            >
              {t('discord.lay.make', 'Kanäle anlegen')}
            </Button>
          </Group>
        )}
      </Stack>
    </Paper>
  )
}

// ── Wer sieht was ────────────────────────────────────────────────────────────

interface AuditRow {
  id: string
  name: string
  kind: string
  parentId: string | null
  access: 'public' | 'members' | 'private'
  uses: string[]
  approved: { by: string; at: string } | null
  problem: string | null
  url: string
}

interface AuditResult {
  channels: AuditRow[]
  findings: { channelId: string; text: string }[]
  server: string[]
  memberRole: string | null
  checkedAt: string
}

function AuditPanel({ connection, canFix }: { connection: Connection; canFix: boolean }) {
  const { t } = useT()
  const { notify } = useToast()
  const queryClient = useQueryClient()
  const key = ['discord-audit', connection.id]
  const query = useQuery({
    queryKey: key,
    queryFn: () => apiFetch<AuditResult>(`/discord/connections/${connection.id}/audit`),
  })
  const [busy, setBusy] = useState<string | null>(null)
  const act = async (row: AuditRow, action: 'approve' | 'revoke' | 'members_only') => {
    setBusy(row.id + action)
    try {
      const result = await apiFetch<AuditResult>(`/discord/connections/${connection.id}/audit/${row.id}`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      queryClient.setQueryData(key, result)
      await queryClient.invalidateQueries({ queryKey: ['discord-server-channels', connection.id] })
      notify(
        'success',
        action === 'approve'
          ? t('discord.au.approved', '#{{name}} bleibt öffentlich', { name: row.name })
          : action === 'revoke'
            ? t('discord.au.revoked', 'Freigabe zurückgenommen')
            : t('discord.au.fixed', '#{{name}} ist jetzt nur für Mitglieder', { name: row.name }),
      )
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
  const data = query.data as AuditResult
  const rows = data.channels.filter((c) => c.kind !== 'category')
  const publicRows = rows.filter((c) => c.access === 'public')
  const label = (a: AuditRow['access']) =>
    a === 'public'
      ? t('discord.au.public', 'Alle auf dem Server')
      : a === 'members'
        ? t('discord.au.members', 'Nur Mitglieder')
        : t('discord.au.private', 'Nur bestimmte Rollen')
  return (
    <Stack gap="sm">
      <Text size="xs" c="dimmed">
        {t(
          'discord.au.hint',
          'Vereinsserver sind für Mitglieder. Ein Kanal, den alle auf dem Server sehen, braucht eine Freigabe hier; sonst steht er als Befund in der Liste und im nächtlichen Bot-Protokoll.',
        )}
      </Text>
      <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="xs">
        <Paper withBorder p="sm">
          <Text size="xs" c="dimmed">{t('discord.au.findings', 'Befunde')}</Text>
          <Text fw={700} size="lg" c={data.findings.length + data.server.length ? 'red' : 'green'}>
            {data.findings.length + data.server.length}
          </Text>
        </Paper>
        <Paper withBorder p="sm">
          <Text size="xs" c="dimmed">{t('discord.au.publicCount', 'Öffentliche Kanäle')}</Text>
          <Text fw={700} size="lg">
            {publicRows.length}
            <Text span size="xs" c="dimmed" fw={400}>
              {' '}
              {t('discord.au.ofWhich', 'davon {{n}} freigegeben', { n: publicRows.filter((r) => r.approved).length })}
            </Text>
          </Text>
        </Paper>
        <Paper withBorder p="sm">
          <Text size="xs" c="dimmed">{t('discord.au.memberRole', 'Mitglied-Rolle')}</Text>
          <Text fw={700} size="lg">
            {data.memberRole ? t('discord.au.on', 'an') : t('discord.au.off', 'aus')}
          </Text>
        </Paper>
      </SimpleGrid>
      {data.server.map((text) => (
        <Alert key={text} color="orange" variant="light">
          {text}
        </Alert>
      ))}
      <Paper withBorder p={0}>
        {rows.map((r, i) => (
          <Group
            key={r.id}
            justify="space-between"
            wrap="wrap"
            gap="xs"
            p="xs"
            px="sm"
            style={{ borderTop: i ? '1px solid var(--mantine-color-default-border)' : undefined }}
          >
            <div style={{ minWidth: 0 }}>
              <Group gap={6} wrap="wrap">
                <Text size="sm" fw={500}>
                  {KIND_ICON[r.kind] ?? '#'} {r.name}
                </Text>
                <Badge
                  size="xs"
                  variant="light"
                  color={r.access === 'public' ? (r.approved ? 'green' : 'orange') : r.access === 'members' ? 'violet' : 'gray'}
                >
                  {label(r.access)}
                </Badge>
                {r.approved && (
                  <Tooltip label={t('discord.au.approvedBy', 'Freigegeben von {{by}}', { by: r.approved.by })}>
                    <Badge size="xs" variant="outline" color="green">
                      {t('discord.au.isApproved', 'freigegeben')}
                    </Badge>
                  </Tooltip>
                )}
                {r.uses.map((u) => (
                  <Badge key={u} size="xs" variant="light" color="blue">
                    {u}
                  </Badge>
                ))}
              </Group>
              {r.problem && (
                <Text size="xs" c="red">
                  {r.problem}
                </Text>
              )}
            </div>
            <Group gap={4} wrap="nowrap">
              {r.access === 'public' && !r.approved && (
                <Button size="compact-xs" variant="default" loading={busy === r.id + 'approve'} onClick={() => act(r, 'approve')}>
                  {t('discord.au.approve', 'Öffentlich lassen')}
                </Button>
              )}
              {r.access === 'public' && r.approved && (
                <Button size="compact-xs" variant="subtle" color="gray" loading={busy === r.id + 'revoke'} onClick={() => act(r, 'revoke')}>
                  {t('discord.au.revoke', 'Freigabe zurücknehmen')}
                </Button>
              )}
              {r.access === 'public' && canFix && (
                <Button size="compact-xs" variant="light" loading={busy === r.id + 'members_only'} onClick={() => act(r, 'members_only')}>
                  {t('discord.au.fix', 'Nur für Mitglieder')}
                </Button>
              )}
              <Anchor href={r.url} target="_blank" rel="noreferrer" size="xs">
                Discord ↗
              </Anchor>
            </Group>
          </Group>
        ))}
      </Paper>
      <Text size="xs" c="dimmed">
        {t('discord.au.checked', 'Geprüft {{at}}. Der Bot prüft jede Nacht und meldet neue Befunde im Protokoll.', {
          at: formatDateTime(data.checkedAt),
        })}
      </Text>
    </Stack>
  )
}

// ── Direktnachricht ──────────────────────────────────────────────────────────

function DmCard({ connection, sources }: { connection: Connection; sources: Options['sources'] }) {
  const { t } = useT()
  const { notify } = useToast()
  const [source, setSource] = useState<string | null>('members')
  const [title, setTitle] = useState('')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const base = `/discord/connections/${connection.id}/dm`
  const count = useQuery({
    queryKey: ['discord-dm-count', connection.id, source],
    queryFn: () => apiFetch<{ count: number; max: number }>(`${base}?source=${encodeURIComponent(source ?? '')}`),
    enabled: !!source,
  })
  const kindLabel: Record<string, string> = {
    members: t('discord.dm.kindMembers', 'Alle'),
    group: t('discord.dm.kindGroup', 'Gruppe'),
    function: t('discord.dm.kindFunction', 'Funktion'),
    tier: t('discord.dm.kindTier', 'Beitragskategorie'),
    role: t('discord.dm.kindRole', 'Hubra-Rolle'),
    type: t('discord.dm.kindType', 'Mitgliedsart'),
  }
  const data = sources.map((s) => ({ value: s.key, label: `${kindLabel[s.kind] ?? s.kind}: ${s.label}` }))
  const n = count.data?.count ?? 0
  const send = async () => {
    setBusy(true)
    try {
      const result = await apiFetch<{ queued: number }>(base, {
        method: 'POST',
        body: JSON.stringify({ source, title, text }),
      })
      notify('success', t('discord.dm.sent', 'Wird an {{n}} Personen gesendet', { n: result.queued }))
      setTitle('')
      setText('')
    } catch (err) {
      notify('error', t('discord.ch.failed', 'Das ging nicht'), (err as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Paper withBorder p="sm" mt="xs">
      <Stack gap="xs">
        <Text fw={600} size="sm">
          {t('discord.dm.title', 'Direktnachricht an Mitglieder')}
        </Text>
        <Text size="xs" c="dimmed">
          {t(
            'discord.dm.hint',
            'Der Bot schickt die Nachricht jedem verknüpften Mitglied der Auswahl, mit Vereinsname und deinem Namen. Wer keine Direktnachrichten vom Server annimmt, wird gezählt, nicht erneut versucht. Höchstens 300 auf einmal.',
          )}
        </Text>
        <Select
          size="xs"
          label={t('discord.dm.to', 'An')}
          data={data}
          value={source}
          searchable
          allowDeselect={false}
          onChange={setSource}
          description={
            count.data
              ? t('discord.dm.count', '{{n}} verknüpfte Mitglieder', { n })
              : undefined
          }
        />
        <TextInput
          size="xs"
          label={t('discord.dm.subject', 'Titel')}
          value={title}
          maxLength={200}
          onChange={(e) => setTitle(e.currentTarget.value)}
        />
        <Textarea
          size="xs"
          label={t('discord.dm.text', 'Text')}
          autosize
          minRows={3}
          maxLength={1800}
          value={text}
          onChange={(e) => setText(e.currentTarget.value)}
        />
        <Group justify="flex-end">
          <Button
            size="xs"
            variant="light"
            loading={busy}
            disabled={!source || !title.trim() || !text.trim() || n === 0}
            onClick={() => setConfirm(true)}
          >
            {t('discord.dm.send', 'Senden')}
          </Button>
        </Group>
      </Stack>
      <ConfirmModal
        open={confirm}
        title={t('discord.dm.confirmTitle', 'Direktnachricht senden?')}
        body={t('discord.dm.confirm', '«{{title}}» geht an {{n}} Personen auf Discord. Das lässt sich nicht zurückholen.', {
          title,
          n,
        })}
        onConfirm={() => {
          setConfirm(false)
          void send()
        }}
        onClose={() => setConfirm(false)}
      />
    </Paper>
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


// ── Moderation, Zahlen, Rückmeldungen ────────────────────────────────────────

interface Leavers {
  people: { memberId: number; name: string; discordId: string }[]
  on: boolean
}

function LeaversCard({ connection }: { connection: Connection }) {
  const { t } = useT()
  const { notify } = useToast()
  const qc = useQueryClient()
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const key = ['discord-leavers', connection.id]
  const q = useQuery({
    queryKey: key,
    queryFn: () => apiFetch<Leavers>(`/discord/connections/${connection.id}/leavers`),
  })
  const run = async () => {
    setBusy(true)
    try {
      const r = await apiFetch<{ removed: number; failed: number }>(
        `/discord/connections/${connection.id}/leavers`,
        { method: 'POST' },
      )
      notify(
        r.failed ? 'warning' : 'success',
        t('discord.leaversDone', '{{n}} vom Server entfernt', { n: r.removed }),
        r.failed ? t('discord.leaversFailed', '{{n}} gingen nicht, siehe Bot-Protokoll', { n: r.failed }) : undefined,
      )
      setConfirm(false)
      qc.invalidateQueries({ queryKey: key })
    } catch (err) {
      notify('error', t('discord.leaversError', 'Entfernen ging nicht'), (err as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Paper withBorder p="sm" radius="md">
      <Group justify="space-between" mb={6} wrap="wrap">
        <Text fw={600} size="sm">
          {t('discord.leaversTitle', 'Ehemalige Mitglieder auf dem Server')}
        </Text>
        <Button
          size="xs"
          color="red"
          variant="light"
          disabled={!q.data?.people.length}
          onClick={() => setConfirm(true)}
        >
          {t('discord.leaversNow', 'Jetzt entfernen')}
        </Button>
      </Group>
      {q.isError && (
        <Text size="sm" c="red">
          {(q.error as Error).message}
        </Text>
      )}
      {q.data && q.data.people.length === 0 && (
        <Text size="sm" c="dimmed">
          {t('discord.leaversNone', 'Niemand: alle Ehemaligen sind schon weg.')}
        </Text>
      )}
      {q.data && q.data.people.length > 0 && (
        <Group gap={6}>
          {q.data.people.map((p) => (
            <Badge key={p.discordId} variant="light" color="gray">
              {p.name}
            </Badge>
          ))}
        </Group>
      )}
      <ConfirmModal
        open={confirm && !busy}
        onClose={() => setConfirm(false)}
        onConfirm={() => void run()}
        title={t('discord.leaversConfirmTitle', 'Vom Server entfernen?')}
        body={t(
          'discord.leaversConfirm',
          '{{n}} ehemalige Mitglieder werden vom Discord-Server entfernt. Sie können über eine Einladung wieder beitreten.',
          { n: q.data?.people.length ?? 0 },
        )}
        confirmLabel={t('discord.leaversNow', 'Jetzt entfernen')}
      />
    </Paper>
  )
}

interface Stats {
  members: number
  joined: number
  left: number
  linked: number
  events: number
  trainings: number
  present: number
  from: string
  to: string
}

function StatsCard({ connection }: { connection: Connection }) {
  const { t } = useT()
  const q = useQuery({
    queryKey: ['discord-stats', connection.id],
    queryFn: () => apiFetch<Stats>(`/discord/connections/${connection.id}/stats`),
  })
  if (!q.data) return null
  const n = q.data
  const cells: [string, string | number][] = [
    [t('discord.stat.members', 'Mitglieder'), n.members],
    [t('discord.stat.new', 'Neu (7 Tage)'), `+${n.joined} / −${n.left}`],
    [
      t('discord.stat.linked', 'Mit Discord'),
      n.members ? `${n.linked} (${Math.round((100 * n.linked) / n.members)} %)` : n.linked,
    ],
    [t('discord.stat.trainings', 'Trainings · eingecheckt'), `${n.trainings} · ${n.present}`],
  ]
  return (
    <Paper withBorder p="sm" radius="md">
      <Text size="xs" c="dimmed" mb={6}>
        {t('discord.statsPreview', 'Letzte Woche, wie am Montag gepostet')}
      </Text>
      <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="sm">
        {cells.map(([label, value]) => (
          <div key={label}>
            <Text size="xs" c="dimmed">
              {label}
            </Text>
            <Text fw={700}>{value}</Text>
          </div>
        ))}
      </SimpleGrid>
    </Paper>
  )
}

interface FeedbackEvent {
  eventId: number
  title: string
  date: string | null
  count: number
  average: number | null
  spread: number[]
  comments: string[]
}

function FeedbackCard({ connection }: { connection: Connection }) {
  const { t } = useT()
  const q = useQuery({
    queryKey: ['discord-feedback', connection.id],
    queryFn: () =>
      apiFetch<{ events: FeedbackEvent[] }>(`/discord/connections/${connection.id}/feedback`),
  })
  const list = q.data?.events ?? []
  return (
    <Paper withBorder p="sm" radius="md">
      <Text fw={600} size="sm" mb={6}>
        {t('discord.feedbackTitle', 'Rückmeldungen nach Anlässen')}
      </Text>
      {list.length === 0 && (
        <Text size="sm" c="dimmed">
          {t('discord.feedbackNone', 'Noch keine. Sie erscheinen hier nach dem nächsten Anlass.')}
        </Text>
      )}
      <Stack gap="sm">
        {list.map((e) => (
          <div key={e.eventId}>
            <Group justify="space-between" wrap="nowrap" gap="xs">
              <Text size="sm" fw={600} truncate>
                {e.title}
              </Text>
              <Text size="sm" style={{ whiteSpace: 'nowrap' }}>
                {e.average != null ? `★ ${e.average.toFixed(1)}` : '–'}{' '}
                <Text span size="xs" c="dimmed">
                  {t('discord.feedbackCount', '{{n}} Antworten', { n: e.count })}
                </Text>
              </Text>
            </Group>
            {e.date && (
              <Text size="xs" c="dimmed">
                {new Date(e.date).toLocaleDateString('de-CH')}
              </Text>
            )}
            {e.comments.slice(-3).map((c, i) => (
              <Text key={i} size="xs" mt={2} style={{ fontStyle: 'italic' }}>
                «{c}»
              </Text>
            ))}
          </div>
        ))}
      </Stack>
    </Paper>
  )
}
