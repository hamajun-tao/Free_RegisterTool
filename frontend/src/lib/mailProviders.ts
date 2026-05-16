export const DEFAULT_PARALLEL_MAIL_MIX = ['luckmail', 'cfworker', 'mail2925']

export const MIX_PROVIDER_OPTIONS = [
  { value: 'luckmail', label: 'LuckMail' },
  { value: 'cfworker', label: 'CF Worker' },
  { value: 'mail2925', label: '2925 Mail' },
  { value: 'moemail', label: 'MoeMail (sall.cc)' },
  { value: 'tempmail_lol', label: 'TempMail.lol' },
  { value: 'skymail', label: 'SkyMail (CloudMail)' },
  { value: 'maliapi', label: 'YYDS Mail / MaliAPI' },
  { value: 'gptmail', label: 'GPTMail' },
  { value: 'opentrashmail', label: 'OpenTrashMail' },
  { value: 'duckmail', label: 'DuckMail' },
  { value: 'duckduckgo', label: 'DuckDuckGo' },
  { value: 'freemail', label: 'Freemail' },
  { value: 'laoudo', label: 'Laoudo' },
]

export function resolveConfiguredMixOptions(cfg: Record<string, any>) {
  const has = (key: string) => String(cfg?.[key] || '').trim().length > 0
  const allowed = new Set<string>()

  if (has('luckmail_api_key')) allowed.add('luckmail')
  if (has('cfworker_api_url')) allowed.add('cfworker')
  if (has('mail2925_login_name') && has('mail2925_password')) allowed.add('mail2925')
  if (has('moemail_api_url') && has('moemail_api_key')) allowed.add('moemail')
  if (has('skymail_api_base') && has('skymail_token')) allowed.add('skymail')
  if (has('maliapi_base_url') && has('maliapi_api_key')) allowed.add('maliapi')
  if (has('gptmail_base_url') && has('gptmail_api_key')) allowed.add('gptmail')
  if (has('opentrashmail_api_url')) allowed.add('opentrashmail')
  if (has('duckmail_api_url') || has('duckmail_provider_url')) allowed.add('duckmail')
  if (
    has('duckduckgo_email') &&
    has('duckduckgo_gmail_address') &&
    (
      has('duckduckgo_gmail_app_password') ||
      (
        String(cfg?.duckduckgo_gmail_api_mode || 'imap').trim() === 'gmail_api' &&
        has('duckduckgo_gmail_api_credentials') &&
        has('duckduckgo_gmail_api_token')
      )
    )
  ) {
    allowed.add('duckduckgo')
  }
  if (has('freemail_api_url')) allowed.add('freemail')
  if (has('laoudo_email') && has('laoudo_auth')) allowed.add('laoudo')
  allowed.add('tempmail_lol')

  const options = MIX_PROVIDER_OPTIONS.filter((item) => allowed.has(item.value))
  return options.length > 0
    ? options
    : MIX_PROVIDER_OPTIONS.filter((item) => DEFAULT_PARALLEL_MAIL_MIX.includes(item.value))
}
