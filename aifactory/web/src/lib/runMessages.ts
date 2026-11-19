import { ApiError, DB_BUSY_CODE } from './api'
import { errorText } from './format'
import { t } from './i18n'
import type { MessageKey } from './i18n/catalogs'

/** The persisted PR error starts with the provider code and, for Forgejo settings, the variable name. */
export function forgejoConfigError(code: string, message: string): string | null {
  const variable = message.match(/^(?:forgejo_not_configured: )?(GIRO_FORGEJO_URL|GIRO_FORGEJO_TOKEN) /)?.[1]
  if (code === 'forgejo_not_configured') {
    return variable ? t('forgejo.missingSetting', { variable }) : t('forgejo.missingConfig')
  }
  if (code !== 'invalid_config' || !variable) return null
  return t(variable === 'GIRO_FORGEJO_URL' ? 'forgejo.invalidUrl' : 'forgejo.invalidToken')
}

export function prErrorText(error: string): string {
  const separator = error.indexOf(': ')
  if (separator === -1) return error
  return forgejoConfigError(error.slice(0, separator), error.slice(separator + 2)) ?? error
}

/** API codes are UI state; the original server message remains diagnostic content. */
export function runError(error: unknown): string {
  if (!(error instanceof ApiError)) return errorText(error)
  const forgejo = forgejoConfigError(error.code, error.message)
  if (forgejo) return forgejo
  const keys: Record<string, MessageKey> = {
    [DB_BUSY_CODE]: 'runs.apiBusy',
    request_timeout: 'runs.apiTimeout',
    unknown_run: 'runs.apiUnknownRun',
    unknown_phase: 'runs.apiUnknownPhase',
    remote_run: 'runs.apiRemote',
    task_busy: 'runs.apiTaskBusy',
    already_running: 'runs.apiTaskBusy',
    invalid_state: 'runs.apiInvalidState',
    run_not_running: 'runs.apiInvalidState',
    run_running: 'runs.apiInvalidState',
  }
  return t(keys[error.code] ?? 'runs.apiFailure', { code: error.code })
}
