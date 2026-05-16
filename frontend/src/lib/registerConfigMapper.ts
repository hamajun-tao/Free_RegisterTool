const REGISTER_EXTRA_KEYS = [
  'mail_provider',
  'mail_provider_mix',
  'laoudo_auth',
  'laoudo_email',
  'laoudo_account_id',
  'gptmail_base_url',
  'gptmail_api_key',
  'gptmail_domain',
  'opentrashmail_api_url',
  'opentrashmail_domain',
  'opentrashmail_password',
  'maliapi_base_url',
  'maliapi_api_key',
  'maliapi_domain',
  'maliapi_auto_domain_strategy',
  'moemail_api_url',
  'moemail_api_key',
  'skymail_api_base',
  'skymail_token',
  'skymail_domain',
  'duckmail_api_url',
  'duckmail_address',
  'duckmail_password',
  'duckduckgo_email',
  'duckduckgo_gmail_address',
  'duckduckgo_gmail_app_password',
  'duckduckgo_imap_host',
  'duckduckgo_imap_port',
  'duckduckgo_mailbox',
  'duckduckgo_all_mailbox',
  'duckduckgo_gmail_api_mode',
  'duckduckgo_gmail_api_credentials',
  'duckduckgo_gmail_api_token',
  'duckduckgo_api_token',
  'duckduckgo_alias_mode',
  'duckduckgo_private_addresses',
  'duckduckgo_alias_rotation',
  'duckmail_provider_url',
  'duckmail_bearer',
  'freemail_api_url',
  'freemail_admin_token',
  'freemail_username',
  'freemail_password',
  'mail2925_login_name',
  'mail2925_password',
  'mail2925_alias_mode',
  'mail2925_domain',
  'cfworker_api_url',
  'cfworker_admin_token',
  'cfworker_custom_auth',
  'cfworker_domain',
  'cfworker_subdomain',
  'cfworker_random_subdomain',
  'cfworker_fingerprint',
  'smsbower_api_key',
  'sms_provider',
  'sim5_api_key',
  'herosms_api_key',
  'smsbower_country',
  'smsbower_type',
  'smsbower_max_price',
  'smsbower_min_price',
  'smsbower_price_steps',
  'smsbower_phone_attempts',
  'smsbower_add_phone_send_attempts',
  'smsbower_otp_timeout_seconds',
  'smsbower_code_attempts',
  'smsbower_provider_ids',
  'smsbower_except_provider_ids',
  'luckmail_base_url',
  'luckmail_api_key',
  'luckmail_email_type',
  'luckmail_domain',
  'yescaptcha_key',
  'solver_url',
  'fraud_guard_proxy_rotations',
  'cpa_api_url',
  'cpa_api_key',
  'sub2api_api_url',
  'sub2api_api_key',
  'sub2api_group_ids',
  'codex_proxy_url',
  'codex_proxy_key',
  'codex_proxy_upload_type',
  'team_manager_url',
  'team_manager_key',
  'cfworker_domain_override',
] as const

import { parseBooleanConfigValue } from '@/lib/configValueParsers'

const BOOLEAN_CONFIG_KEYS = new Set(['cfworker_random_subdomain'])

export function buildRegisterExtra(
  cfg: Record<string, any>,
  values: Record<string, any>,
): Record<string, any> {
  const extra: Record<string, any> = {}

  for (const key of REGISTER_EXTRA_KEYS) {
    const val = values[key]
    if (val !== undefined && val !== null && val !== '') {
      extra[key] = val
    } else if (cfg[key] !== undefined && cfg[key] !== null && cfg[key] !== '') {
      extra[key] = BOOLEAN_CONFIG_KEYS.has(key) ? parseBooleanConfigValue(cfg[key]) : cfg[key]
    }
  }

  if (values.mail_provider_mix_enabled) {
    extra.mail_provider_mix = values.mail_provider_mix || []
  } else if (!extra.mail_provider_mix) {
    extra.mail_provider_mix = []
  }

  if (values.mail_provider) {
    extra.mail_provider = values.mail_provider
  } else if (!extra.mail_provider) {
    extra.mail_provider = cfg.mail_provider || 'luckmail'
  }

  return extra
}
