import { Button } from '@mantine/core'
import { apiFetch, registerPlugin, useT, useToast } from '@hubra/plugin-sdk'
import type { SlotProps } from '@hubra/plugin-sdk'
import translations from './translations.json'

/** In the module's settings dialog: post a test message to the webhook. */
function TestButton({ code, configured, name }: SlotProps['settings.module.actions']) {
  const { t } = useT()
  const { notify } = useToast()
  if (code !== 'discord' || !configured) return null
  return (
    <Button
      variant="subtle"
      mr="auto"
      onClick={async () => {
        try {
          await apiFetch<{ ok: boolean }>('/discord/test', { method: 'POST' })
          notify('success', t('settings.modules.discordTestSent', 'Test message sent'))
        } catch (err) {
          notify('error', name, (err as Error).message)
        }
      }}
    >
      {t('settings.modules.discordTest', 'Send test message')}
    </Button>
  )
}

registerPlugin({
  id: 'discord',
  slots: [{ slot: 'settings.module.actions', module: 'discord', component: TestButton }],
  translations,
})
