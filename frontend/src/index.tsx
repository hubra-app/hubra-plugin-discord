import { useEffect, useState } from 'react'
import { ActionIcon, Alert, Button, Group, Select, Stack, Switch, Text } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { getConnectionOptions, registerPlugin, updateConnection, useT, useToast } from '@hubra/plugin-sdk'
import type { Connection, SlotProps } from '@hubra/plugin-sdk'
import translations from './translations.json'

interface Options {
  channels: { id: string; name: string }[]
  roles: { id: string; name: string; assignable: boolean }[]
  sources: { key: string; label: string; kind: string }[]
}

interface RoleMapping {
  source: string
  role_id: string
}

interface DiscordConfig {
  channel_id: string | null
  announce_events: boolean
  announce_posts: boolean
  announce_polls: boolean
  members_only_events: boolean
  mention: string
  role_map: RoleMapping[]
}

function readConfig(c: Connection): DiscordConfig {
  const raw = c.config as Partial<DiscordConfig>
  return {
    channel_id: raw.channel_id ?? null,
    announce_events: raw.announce_events ?? true,
    announce_posts: raw.announce_posts ?? true,
    announce_polls: raw.announce_polls ?? false,
    members_only_events: raw.members_only_events ?? false,
    mention: raw.mention ?? '',
    role_map: raw.role_map ?? [],
  }
}

/** In Einstellungen › Verbundene Apps: the Discord connection's settings. */
function DiscordSettings({ connection, onChange }: SlotProps['apps.connection']) {
  const { t } = useT()
  const { notify } = useToast()
  const [config, setConfig] = useState(() => readConfig(connection))
  const [busy, setBusy] = useState(false)
  useEffect(() => setConfig(readConfig(connection)), [connection])

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
  const set = (patch: Partial<DiscordConfig>) => setConfig((c) => ({ ...c, ...patch }))
  const roleName = (id: string) => options.data.roles.find((r) => r.id === id)?.name ?? id

  const save = async () => {
    setBusy(true)
    try {
      const sent: Partial<DiscordConfig> = {}
      if (announce) {
        Object.assign(sent, {
          channel_id: config.channel_id,
          announce_events: config.announce_events,
          announce_posts: config.announce_posts,
          announce_polls: config.announce_polls,
          members_only_events: config.members_only_events,
          mention: config.mention,
        })
      }
      if (roles) sent.role_map = config.role_map.filter((m) => m.source && m.role_id)
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
      {announce && (
        <>
          <Text fw={600} size="sm">
            {t('discord.announcements', 'Ankündigungen')}
          </Text>
          <Select
            label={t('discord.channel', 'Kanal')}
            placeholder={t('discord.pickChannel', 'Kanal wählen')}
            data={options.data.channels.map((c) => ({ value: c.id, label: `#${c.name}` }))}
            value={config.channel_id}
            onChange={(v) => set({ channel_id: v })}
            searchable
            clearable
          />
          <Group gap="lg">
            <Switch
              label={t('discord.events', 'Neue Anlässe')}
              checked={config.announce_events}
              onChange={(e) => set({ announce_events: e.currentTarget.checked })}
            />
            <Switch
              label={t('discord.posts', 'Neue Beiträge')}
              checked={config.announce_posts}
              onChange={(e) => set({ announce_posts: e.currentTarget.checked })}
            />
            <Switch
              label={t('discord.polls', 'Neue Umfragen')}
              checked={config.announce_polls}
              onChange={(e) => set({ announce_polls: e.currentTarget.checked })}
            />
          </Group>
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
                  set({ role_map: config.role_map.map((x, j) => (j === i ? { ...x, source: v ?? '' } : x)) })
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
                  set({ role_map: config.role_map.map((x, j) => (j === i ? { ...x, role_id: v ?? '' } : x)) })
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
          {config.role_map.some((m) => m.role_id && !options.data.roles.find((r) => r.id === m.role_id)?.assignable) && (
            <Alert color="orange" variant="light">
              {t('discord.notAssignable', 'Der Bot kann diese Rolle nicht vergeben: {{roles}}', {
                roles: config.role_map
                  .filter((m) => m.role_id && !options.data.roles.find((r) => r.id === m.role_id)?.assignable)
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

      {(announce || roles) && (
        <Group justify="flex-end">
          <Button onClick={save} loading={busy}>
            {t('common.save', 'Speichern')}
          </Button>
        </Group>
      )}
    </Stack>
  )
}

registerPlugin({
  id: 'discord',
  slots: [{ slot: 'apps.connection', module: 'discord', perm: 'apps.apps.manage', component: DiscordSettings }],
  translations,
})
