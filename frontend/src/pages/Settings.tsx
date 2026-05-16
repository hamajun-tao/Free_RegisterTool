import { useEffect, useMemo, useState } from 'react'
import { App, Alert, Card, Form, Input, Select, Button, message, Segmented, Tabs, Space, Tag, Typography, Modal, QRCode, Switch, Collapse } from 'antd'
import {
  SaveOutlined,
  EyeOutlined,
  EyeInvisibleOutlined,
  MailOutlined,
  SafetyOutlined,
  ApiOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  SyncOutlined,
  PlusOutlined,
  LockOutlined,
} from '@ant-design/icons'
import { parseBooleanConfigValue } from '@/lib/configValueParsers'
import { apiFetch } from '@/lib/utils'
import { PageHeader } from '@/components/ui'

const SELECT_FIELDS: Record<string, { label: string; value: string }[]> = {
  chatgpt_phone_source: [
    { label: 'SMSBOWER', value: 'smsbower' },
    { label: '5SIM', value: '5sim' },
    { label: 'HeroSMS', value: 'herosms' },
    { label: 'SMSToMe', value: 'smstome' },
  ],
  mail_provider: [
    { label: 'LuckMail（订单接码 / 已购邮箱）', value: 'luckmail' },
    { label: 'Laoudo（固定邮箱）', value: 'laoudo' },
    { label: 'TempMail.lol（自动生成）', value: 'tempmail_lol' },
    { label: 'SkyMail（CloudMail 接口）', value: 'skymail' },
    { label: 'DuckMail（自动生成）', value: 'duckmail' },
    { label: 'DuckDuckGo Email Protection', value: 'duckduckgo' },
    { label: 'MoeMail (sall.cc)', value: 'moemail' },
    { label: 'YYDS Mail / MaliAPI', value: 'maliapi' },
    { label: 'GPTMail', value: 'gptmail' },
    { label: 'OpenTrashMail', value: 'opentrashmail' },
    { label: '2925 Mail (Web)', value: 'mail2925' },
    { label: 'Freemail（自建 CF Worker）', value: 'freemail' },
    { label: 'CF Worker（自建域名）', value: 'cfworker' },
  ],
  maliapi_auto_domain_strategy: [
    { label: 'balanced', value: 'balanced' },
    { label: 'prefer_owned', value: 'prefer_owned' },
    { label: 'prefer_public', value: 'prefer_public' },
  ],
  default_executor: [
    { label: 'API 协议（无浏览器）', value: 'protocol' },
    { label: '无头浏览器', value: 'headless' },
    { label: '有头浏览器', value: 'headed' },
  ],
  default_captcha_solver: [
    { label: 'YesCaptcha', value: 'yescaptcha' },
    { label: '本地 Solver (Camoufox)', value: 'local_solver' },
    { label: '手动', value: 'manual' },
  ],
  cpa_cleanup_enabled: [
    { label: '关闭', value: '0' },
    { label: '开启', value: '1' },
  ],
  codex_proxy_upload_type: [
    { label: 'AT（Access Token，推荐）', value: 'at' },
    { label: 'RT（Refresh Token）', value: 'rt' },
  ],
  smsbower_type: [
    { label: '任意质量', value: '' },
    { label: 'Gold（高成功率）', value: 'gold' },
    { label: 'Silver（标准）', value: 'silver' },
  ],
  sms_provider: [
    { label: 'SMSBOWER', value: 'smsbower' },
    { label: '5SIM', value: '5sim' },
    { label: 'HeroSMS', value: 'herosms' },
  ],
}

const TAB_LABEL_OVERRIDES: Record<string, string> = {
  register: '注册设置',
  mailbox: '邮箱服务',
  captcha: '验证码',
  chatgpt: 'ChatGPT',
  cliproxyapi: 'CLIProxyAPI',
  grok: 'Grok',
  kiro: 'Kiro',
  integrations: '插件',
  security: '安全',
  claude: 'Claude 导入',
}

const SECTION_UI_OVERRIDES: Record<string, { title?: string; desc?: string }> = {
  default_executor: { title: '默认注册方式', desc: '控制注册任务如何执行' },
  mail_provider: { title: '默认邮箱服务', desc: '选择注册时使用的邮箱服务' },
  laoudo: { title: 'Laoudo', desc: '固定邮箱，手动配置使用' },
  freemail: { title: 'Freemail', desc: '基于 Cloudflare Worker 的自建邮箱服务' },
  mail2925: { title: '2925 Mail', desc: '使用 2925 WebMail 会话收件箱' },
  moemail: { title: 'MoeMail', desc: '自动创建临时邮箱并轮询邮件' },
  skymail: { title: 'SkyMail', desc: 'CloudMail 兼容接口' },
  maliapi: { title: 'MaliAPI', desc: '基于 API Key 的临时邮箱服务' },
  gptmail: { title: 'GPTMail', desc: '通过 GPTMail API 生成临时邮箱' },
  opentrashmail: { title: 'OpenTrashMail', desc: '兼容 OpenTrashMail 服务端接口' },
  tempmail_lol: { title: 'TempMail.lol', desc: '开箱即用，无需额外配置' },
  duckmail: { title: 'DuckMail', desc: '自动生成地址并拉取邮件' },
  duckduckgo: { title: 'DuckDuckGo Email Protection', desc: '通过 Duck 地址和 Gmail 收取验证码邮件' },
  cfworker: { title: 'CF Worker 邮箱', desc: '自建 Worker 邮箱服务' },
  sms_provider: { title: 'SMS 接码', desc: '用于 ChatGPT add-phone 阶段' },
  default_captcha_solver: { title: '验证码服务', desc: '配置注册过程中的打码方式' },
  cpa_api_url: { title: 'CPA 面板', desc: '注册完成后自动上传到 CPA 平台' },
  sub2api_api_url: { title: 'Sub2API 面板', desc: '注册完成后自动上传到 Sub2API 平台' },
  cpa_cleanup_enabled: { title: 'CPA 自动维护', desc: '定时清理错误凭证并自动补注册' },
  team_manager_url: { title: 'Team Manager', desc: '上传到自建 Team Manager 系统' },
  codex_proxy_url: { title: 'CodexProxy', desc: '注册完成后自动上传到 CodexProxy 平台' },
  smstome_cookie: { title: 'SMSToMe 手机验证', desc: 'ChatGPT add-phone 阶段自动取号和收码' },
  cliproxyapi_base_url: { title: 'CLIProxyAPI', desc: '用于 CLIProxyAPI 管理页登录' },
  grok2api_url: { title: 'grok2api', desc: '注册成功后自动导入 grok2api' },
  kiro_manager_path: { title: 'Kiro Account Manager', desc: '注册成功后自动写入本地账号文件' },
}

const FIELD_UI_OVERRIDES: Record<string, Partial<FieldConfig>> = {
  sms_provider: { label: '在线接码平台' },
  smsbower_country: { label: '国家代码列表' },
  smsbower_type: { label: '号码质量' },
  smsbower_max_price: { label: '最高单价（USD）' },
  smsbower_min_price: { label: '最低单价（USD，可选）' },
  smsbower_phone_attempts: { label: '每国取号次数' },
  smsbower_add_phone_send_attempts: { label: 'add-phone 发送尝试' },
  smsbower_otp_timeout_seconds: { label: '等码超时（秒）' },
  smsbower_code_attempts: { label: '验证码重试次数' },
  smsbower_provider_ids: { label: '指定供应商 ID' },
  smsbower_except_provider_ids: { label: '排除供应商 ID' },
  smstome_country_slugs: { label: '国家列表' },
  smstome_phone_attempts: { label: '手机号尝试次数' },
  smstome_otp_timeout_seconds: { label: '等码超时（秒）' },
  smstome_poll_interval_seconds: { label: '轮询间隔（秒）' },
  smstome_sync_max_pages_per_country: { label: '每国同步页数' },
  mail2925_alias_mode: { label: '别名模式' },
  mail2925_domain: { label: '别名域名' },
  duckduckgo_gmail_api_mode: { label: 'Gmail 模式' },
  duckduckgo_alias_mode: { label: 'Duck 地址模式' },
  duckduckgo_alias_rotation: { label: 'Duck 地址轮换' },
  luckmail_email_type: { label: '邮箱类型' },
  codex_proxy_upload_type: { label: '上传类型' },
  cliproxyapi_management_key: { label: '管理口令' },
}

const SELECT_OPTION_OVERRIDES: Record<string, Record<string, string>> = {
  default_executor: {
    protocol: 'API 协议（无浏览器）',
    headless: '无头浏览器',
    headed: '有头浏览器',
  },
  default_captcha_solver: {
    yescaptcha: 'YesCaptcha',
    local_solver: '本地 Solver (Camoufox)',
    manual: '手动',
  },
  mail2925_alias_mode: {
    plus: 'plus',
    main: 'main',
    random: 'random',
  },
  duckduckgo_gmail_api_mode: {
    imap: 'IMAP',
    gmail_api: 'Gmail API',
  },
  duckduckgo_alias_mode: {
    fixed: '固定地址',
    pool: '地址池',
    auto_generate: '自动生成',
  },
  duckduckgo_alias_rotation: {
    random: '随机',
    round_robin: '轮询',
  },
  luckmail_email_type: {
    ms_graph: 'Microsoft Graph',
    ms_imap: 'Microsoft IMAP',
    self_built: '自建邮箱',
  },
  codex_proxy_upload_type: {
    at: 'AT (Access Token)',
    rt: 'RT (Refresh Token)',
  },
}

function getSectionKey(section: SectionConfig) {
  return section.provider || section.fields[0]?.key || section.title
}

function getSectionDisplayTitle(section: SectionConfig) {
  return SECTION_UI_OVERRIDES[getSectionKey(section)]?.title || section.title
}

function sectionHasField(section: SectionConfig, fieldKey: string) {
  return section.fields.some((field) => field.key === fieldKey)
}

function isSmsProviderSection(section: SectionConfig) {
  return sectionHasField(section, 'sms_provider')
}

function isSmstomeSection(section: SectionConfig) {
  return sectionHasField(section, 'smstome_cookie')
}

function isLegacyPaymentSection(section: SectionConfig) {
  return sectionHasField(section, 'payment_auto_plan')
}

const TAB_ITEMS = [
  {
    key: 'register',
    label: '注册设置',
    icon: <ApiOutlined />,
    sections: [
      {
        title: '默认注册方式',
        desc: '控制注册任务如何执行',
        fields: [{ key: 'default_executor', label: '执行器类型', type: 'select' }],
      },
    ],
  },
  {
    key: 'mailbox',
    label: '邮箱服务',
    icon: <MailOutlined />,
    sections: [
      {
        title: '默认邮箱服务',
        desc: '选择注册时使用的邮箱类型',
        fields: [{ key: 'mail_provider', label: '邮箱服务', type: 'select' }],
      },
      {
        title: 'Laoudo',
        provider: 'laoudo',
        desc: '固定邮箱，手动配置',
        fields: [
          { key: 'laoudo_email', label: '邮箱地址', placeholder: 'xxx@laoudo.com' },
          { key: 'laoudo_account_id', label: 'Account ID', placeholder: '563' },
          { key: 'laoudo_auth', label: 'JWT Token', placeholder: 'eyJ...', secret: true },
        ],
      },
      {
        title: 'Freemail',
        provider: 'freemail',
        desc: '基于 Cloudflare Worker 的自建邮箱，支持管理员令牌或账号密码认证',
        fields: [
          { key: 'freemail_api_url', label: 'API URL', placeholder: 'https://mail.example.com' },
          { key: 'freemail_admin_token', label: '管理员令牌', secret: true },
          { key: 'freemail_username', label: '用户名（可选）' },
          { key: 'freemail_password', label: '密码（可选）', secret: true },
        ],
      },
      {
        title: '2925 Mail',
        provider: 'mail2925',
        desc: 'Use 2925 webmail session inbox. Login uses the 2925 main account prefix and reads inbox via web APIs.',
        fields: [
          { key: 'mail2925_login_name', label: 'Login Name', placeholder: 'yourname' },
          { key: 'mail2925_password', label: 'Password', secret: true },
          { key: 'mail2925_alias_mode', label: 'Alias Mode', placeholder: 'plus / main / random' },
          { key: 'mail2925_domain', label: 'Alias Domain', placeholder: '2925.com' },
        ],
      },
      {
        title: 'MoeMail',
        provider: 'moemail',
        desc: '自动注册账号并生成临时邮箱',
        fields: [
          { key: 'moemail_api_url', label: 'API URL', placeholder: 'https://sall.cc' },
          { key: 'moemail_api_key', label: 'API Key', secret: true },
        ],
      },
      {
        title: 'SkyMail',
        provider: 'skymail',
        desc: 'CloudMail 兼容接口（addUser / emailList）',
        fields: [
          { key: 'skymail_api_base', label: 'API Base', placeholder: 'https://api.skymail.ink' },
          { key: 'skymail_token', label: 'Authorization Token', secret: true },
          { key: 'skymail_domain', label: '邮箱域名', placeholder: 'mail.example.com' },
        ],
      },
      {
        title: 'YYDS Mail / MaliAPI',
        provider: 'maliapi',
        desc: '基于 API Key 创建临时邮箱并轮询收件箱消息',
        fields: [
          { key: 'maliapi_base_url', label: 'API URL', placeholder: 'https://maliapi.215.im/v1' },
          { key: 'maliapi_api_key', label: 'API Key', secret: true },
          { key: 'maliapi_domain', label: '邮箱域名（可选）', placeholder: 'example.com' },
          { key: 'maliapi_auto_domain_strategy', label: '自动域名策略', type: 'select' },
        ],
      },
      {
        title: 'GPTMail',
        provider: 'gptmail',
        desc: '基于 GPTMail API 生成临时邮箱并轮询邮件；若已知本站可用域名，也可本地拼装随机地址',
        fields: [
          { key: 'gptmail_base_url', label: 'API URL', placeholder: 'https://mail.chatgpt.org.uk' },
          { key: 'gptmail_api_key', label: 'API Key', secret: true, placeholder: 'gpt-test' },
          { key: 'gptmail_domain', label: '邮箱域名（可选）', placeholder: 'example.com' },
        ],
      },
      {
        title: 'OpenTrashMail',
        provider: 'opentrashmail',
        desc: '对接 opentrashmail 服务；可直接轮询 /json/<email>，也支持已知域名时本地拼装随机地址',
        fields: [
          { key: 'opentrashmail_api_url', label: 'API URL', placeholder: 'http://mail.example.com:8085' },
          { key: 'opentrashmail_domain', label: '邮箱域名（可选）', placeholder: 'xiyoufm.com' },
          { key: 'opentrashmail_password', label: '站点密码（可选）', secret: true, placeholder: '启用 PASSWORD 时填写' },
        ],
      },
      {
        title: 'TempMail.lol',
        provider: 'tempmail_lol',
        desc: '自动生成邮箱，无需配置，需要代理访问（CN IP 被封）',
        fields: [],
      },
      {
        title: 'DuckMail',
        provider: 'duckmail',
        desc: '自动生成邮箱，随机创建账号',
        fields: [
          { key: 'duckmail_api_url', label: 'Web URL', placeholder: 'https://www.duckmail.sbs' },
          { key: 'duckmail_provider_url', label: 'Provider URL', placeholder: 'https://api.duckmail.sbs' },
          { key: 'duckmail_bearer', label: 'Bearer Token', placeholder: 'kevin273945', secret: true },
          { key: 'duckmail_domain', label: '自定义域名', placeholder: '留空则从 Provider URL 推导' },
          { key: 'duckmail_api_key', label: 'API Key（私有域名）', placeholder: 'dk_xxx（domain.duckmail.sbs 获取）', secret: true },
        ],
      },
      {
        title: 'DuckDuckGo Email Protection',
        provider: 'duckduckgo',
        desc: '?? @duck.com ????? Gmail IMAP ? Gmail API ???? OTP',
        fields: [
          { key: 'duckduckgo_email', label: 'Duck Address', placeholder: 'name@duck.com' },
          { key: 'duckduckgo_gmail_address', label: 'Gmail Address', placeholder: 'name@gmail.com' },
          { key: 'duckduckgo_gmail_app_password', label: 'Gmail App Password', placeholder: '16-char app password', secret: true },
          { key: 'duckduckgo_gmail_api_mode', label: 'Gmail Mode', placeholder: 'imap / gmail_api' },
          { key: 'duckduckgo_imap_host', label: 'IMAP Host', placeholder: 'imap.gmail.com' },
          { key: 'duckduckgo_imap_port', label: 'IMAP Port', placeholder: '993' },
          { key: 'duckduckgo_mailbox', label: 'Mailbox', placeholder: 'INBOX' },
          { key: 'duckduckgo_all_mailbox', label: 'All Mailbox', placeholder: '[Gmail]/All Mail' },
          { key: 'duckduckgo_gmail_api_credentials', label: 'Gmail API Credentials JSON', placeholder: '{"installed": {...}}', secret: true },
          { key: 'duckduckgo_gmail_api_token', label: 'Gmail API Token JSON', placeholder: '{"refresh_token": "..."}', secret: true },
          { key: 'duckduckgo_api_token', label: 'Duck API Token', placeholder: 'Duck Email Protection token', secret: true },
          { key: 'duckduckgo_alias_mode', label: 'Duck Address Mode', placeholder: 'fixed / pool / auto_generate' },
          { key: 'duckduckgo_alias_rotation', label: 'Duck Rotation', placeholder: 'random / round_robin' },
          { key: 'duckduckgo_private_addresses', label: 'Duck Private Addresses', placeholder: 'one@duck.com\ntwo@duck.com' },
        ],
      },
      {
        title: 'CF Worker 自建邮箱',
        provider: 'cfworker',
        desc: '基于 Cloudflare Worker 的自建临时邮箱服务',
        fields: [
          { key: 'cfworker_api_url', label: 'API URL', placeholder: 'https://apimail.example.com' },
          { key: 'cfworker_admin_token', label: '管理员 Token', secret: true },
          { key: 'cfworker_custom_auth', label: '站点密码', secret: true },
          { key: 'cfworker_subdomain', label: '固定子域名', placeholder: 'mail / pool-a' },
          { key: 'cfworker_random_subdomain', label: '随机子域名', type: 'boolean' },
          { key: 'cfworker_fingerprint', label: 'Fingerprint', placeholder: '6703363b...' },
        ],
      },
      {
        title: 'LuckMail',
        provider: 'luckmail',
        desc: 'ChatGPT 走购买邮箱，其他平台继续走订单接码老逻辑',
        fields: [
          { key: 'luckmail_base_url', label: '平台地址', placeholder: 'https://mails.luckyous.com' },
          { key: 'luckmail_api_key', label: 'API Key', secret: true },
          { key: 'luckmail_email_type', label: '邮箱类型（可选）', placeholder: 'ms_graph / ms_imap / self_built' },
          { key: 'luckmail_domain', label: '邮箱域名（可选）', placeholder: 'outlook.com / gmail.com' },
        ],
      },
      {
        title: 'SMSBOWER 手机接码',
        desc: '用于 ChatGPT add-phone 阶段；国家代码由 SMSBOWER 提供，质量支持任意 / gold / silver',
        fields: [
          { key: 'sms_provider', label: '接码平台', type: 'select' },
          { key: 'smsbower_api_key', label: 'API Key', secret: true },
          { key: 'sim5_api_key', label: '5SIM API Key', secret: true },
          { key: 'herosms_api_key', label: 'HeroSMS API Key', secret: true },
          { key: 'smsbower_country', label: '国家代码', placeholder: '例如 12,10,22,6,52,78（美国、越南、英国、印尼、泰国、法国）' },
          { key: 'smsbower_type', label: '号码质量', type: 'select' },
          { key: 'smsbower_max_price', label: '最高单价（美元）', placeholder: '例如 0.09；买不到号时调高' },
          { key: 'smsbower_min_price', label: '最低单价（美元，可选）', placeholder: '通常留空' },
          { key: 'smsbower_phone_attempts', label: '每国最多取号次数', placeholder: '默认 12' },
          { key: 'smsbower_add_phone_send_attempts', label: 'add-phone 尝试次数', placeholder: '默认 8' },
          { key: 'smsbower_otp_timeout_seconds', label: '短信等待秒数', placeholder: '默认 120' },
          { key: 'smsbower_code_attempts', label: '验证码提交/重发次数', placeholder: '默认 2' },
          { key: 'fraud_guard_proxy_rotations', label: 'fraud_guard 换代理次数', placeholder: '默认 3' },
          { key: 'smsbower_provider_ids', label: '指定供应商 ID（可选）', placeholder: '多个用英文逗号分隔，例如 2260,2920' },
          { key: 'smsbower_except_provider_ids', label: '排除供应商 ID（可选）', placeholder: '多个用英文逗号分隔，例如 2217' },
        ],
      },
    ],
  },
  {
    key: 'captcha',
    label: '验证码',
    icon: <SafetyOutlined />,
    sections: [
      {
        title: '验证码服务',
        desc: '用于绕过注册页面的人机验证',
        fields: [
          { key: 'default_captcha_solver', label: '默认服务', type: 'select' },
          { key: 'yescaptcha_key', label: 'YesCaptcha Key', secret: true },
        ],
      },
    ],
  },
  {
    key: 'chatgpt',
    label: 'ChatGPT',
    icon: <ApiOutlined />,
    sections: [
      {
        title: 'CPA 面板',
        desc: '注册完成后自动上传到 CPA 管理平台',
        fields: [
          { key: 'cpa_api_url', label: 'API URL', placeholder: 'https://your-cpa.example.com' },
          { key: 'cpa_api_key', label: 'API Key', secret: true },
        ],
      },
      {
        title: 'Sub2API 面板',
        desc: '注册完成后自动上传到 Sub2API 管理后台',
        fields: [
          { key: 'sub2api_api_url', label: 'API URL', placeholder: 'https://your-sub2api.example.com' },
          { key: 'sub2api_api_key', label: 'API Key', secret: true },
          { key: 'sub2api_group_ids', label: '分组 ID', placeholder: '多个分组用英文逗号分隔，例如 2,4,8' },
        ],
      },
      {
        title: 'CPA 自动维护',
        desc: '定时删除 status=error 的凭证，剩余数量低于阈值时自动按现有配置补注册 ChatGPT',
        fields: [
          { key: 'cpa_cleanup_enabled', label: '自动维护', type: 'select' },
          { key: 'cpa_cleanup_interval_minutes', label: '检查间隔（分钟）', placeholder: '60' },
          { key: 'cpa_cleanup_threshold', label: '最低凭证阈值', placeholder: '5' },
          { key: 'cpa_cleanup_concurrency', label: '补注册并发数', placeholder: '1' },
          { key: 'cpa_cleanup_register_delay_seconds', label: '每个注册延迟（秒）', placeholder: '0' },
        ],
      },
      {
        title: 'Team Manager',
        desc: '上传到自建 Team Manager 系统',
        fields: [
          { key: 'team_manager_url', label: 'API URL', placeholder: 'https://your-tm.example.com' },
          { key: 'team_manager_key', label: 'API Key', secret: true },
        ],
      },
      {
        title: 'CodexProxy',
        desc: '注册完成后自动上传到 CodexProxy 管理平台',
        fields: [
          { key: 'codex_proxy_url', label: 'API URL', placeholder: 'https://your-codex-proxy.example.com' },
          { key: 'codex_proxy_key', label: 'Admin Key', secret: true },
          { key: 'codex_proxy_upload_type', label: '上传类型' },
        ],
      },
      {
        title: 'SMSToMe 手机验证',
        desc: 'ChatGPT add_phone 阶段自动取号并轮询短信验证码',
        fields: [
          { key: 'smstome_cookie', label: 'SMSToMe Cookie', secret: true },
          { key: 'smstome_country_slugs', label: '国家列表', placeholder: 'united-states,united-kingdom,poland' },
          { key: 'smstome_phone_attempts', label: '手机号尝试次数', placeholder: '3' },
          { key: 'smstome_otp_timeout_seconds', label: '短信等待秒数', placeholder: '45' },
          { key: 'smstome_poll_interval_seconds', label: '轮询间隔秒数', placeholder: '5' },
          { key: 'smstome_sync_max_pages_per_country', label: '每国同步页数', placeholder: '5' },
        ],
      },
      {
        title: '⚡ 自动支付升级配置',
        desc: '配置后注册账号将自动完成 Plus/Team 升级，无需手动操作',
        fields: [
          { key: 'payment_auto_plan', label: '自动升级套餐', type: 'select' },
          { key: 'payment_plus_flow_order', label: 'Plus 支付时机', type: 'select' },
          { key: 'payment_method', label: '支付方式', type: 'select' },
          { key: 'payment_skip_if_not_free', label: '⭐ 不免费就跳过（保留 Free）', type: 'select' },
          { key: 'payment_auto_cancel_after_subscribe', label: '⭐ 开通后自动取消订阅（防续费）', type: 'select' },
          { key: 'payment_gopay_auto_register', label: '⭐ GoPay 全自动注册（无限开 Plus）', type: 'select' },
          { key: 'payment_card_number', label: '信用卡号', placeholder: '4242424242424242', secret: true },
          { key: 'payment_card_exp_month', label: '到期月（MM）', placeholder: '12' },
          { key: 'payment_card_exp_year', label: '到期年（YYYY）', placeholder: '2027' },
          { key: 'payment_card_cvc', label: 'CVC / CVV', placeholder: '123', secret: true },
          { key: 'payment_paypal_email', label: 'PayPal 账号', placeholder: 'paypal@example.com（仅 PayPal 模式）' },
          { key: 'payment_paypal_password', label: 'PayPal 密码', secret: true, placeholder: '仅 PayPal 模式' },
          { key: 'payment_gopay_phone', label: 'GoPay 手机号', placeholder: '8123456789（印尼号无需+62前缀，仅 GoPay 模式）' },
          { key: 'payment_gopay_pin', label: 'GoPay PIN 码', secret: true, placeholder: '6位数字（仅 GoPay 模式）' },
          { key: 'payment_gopay_otp_file', label: 'GoPay 自动接码文件路径', placeholder: '留空则使用 runtime/wa_relay/wa-otp.txt' },
          { key: 'payment_gopay_otp_url', label: 'GoPay 自动接码 URL', placeholder: '例如 http://127.0.0.1:8765/latest（本地扫码中间件或 WhatsApp Webhook 都可）' },
          { key: 'payment_gopay_sms_country', label: 'GoPay 接码国家', placeholder: 'SMSBOWER 国家代码，默认 6（印尼）' },
          { key: 'payment_gopay_sms_service', label: 'GoPay 接码服务', placeholder: 'SMSBOWER 服务代码，默认 ot' },
          { key: 'wa_relay_src_dir', label: 'WhatsApp Relay 源码目录', placeholder: '例如 C:\\Desktop\\Gpt-Agreement-Payment-main\\Gpt-Agreement-Payment-main\\webui\\whatsapp_relay' },
          { key: 'wa_relay_proxy_url', label: 'WhatsApp Relay 代理', placeholder: '例如 http://127.0.0.1:7897（WhatsApp WebSocket 超时时填写）' },
          { key: 'payment_card_py_path', label: 'CTF-pay card.py 路径', placeholder: '例如 C:\\Desktop\\Gpt-Agreement-Payment-main\\Gpt-Agreement-Payment-main\\CTF-pay\\card.py' },
          { key: 'payment_python_executable', label: '支付独立 Python', placeholder: '例如 E:\\ctf-pay\\python.exe（留空则复用当前后端环境）' },
          { key: 'payment_captcha_validate_online', label: '在线校验打码 Key', type: 'select' },
          { key: 'payment_is_coupon_from_query_param', label: 'Coupon 来源参数', type: 'select' },
          { key: 'payment_checkout_ui_mode', label: 'Checkout UI 模式', type: 'select' },
          { key: 'payment_vlm_base_url', label: 'hCaptcha VLM Base URL', placeholder: '例如 https://api.openai.com/v1 或 https://lucen.cc' },
          { key: 'payment_vlm_api_key', label: 'hCaptcha VLM API Key', secret: true },
          { key: 'payment_vlm_model', label: 'hCaptcha VLM 模型', placeholder: 'gpt-4o' },
          { key: 'payment_vlm_timeout_s', label: 'hCaptcha VLM 超时秒数', placeholder: '45' },
          { key: 'payment_billing_name', label: '持卡人姓名', placeholder: 'John Smith' },
          { key: 'payment_billing_country', label: '账单国家', type: 'select' },
          { key: 'payment_billing_address', label: '账单地址', placeholder: '123 Main St' },
          { key: 'payment_billing_city', label: '城市', placeholder: 'New York' },
          { key: 'payment_billing_state', label: '州/省份', placeholder: 'NY' },
          { key: 'payment_billing_zip', label: '邮政编码', placeholder: '10001' },
          { key: 'payment_captcha_api_url', label: 'hCaptcha 打码平台 URL', placeholder: 'https://api.yescaptcha.com（兼容 YesCaptcha 协议）' },
          { key: 'payment_captcha_key', label: '打码平台 Client Key', secret: true },
          { key: 'payment_team_workspace_name', label: 'Team Workspace 名称', placeholder: 'MyWorkspace（仅 Team 套餐需填）' },
          { key: 'payment_team_seat_quantity', label: 'Team 席位数量', placeholder: '5（仅 Team 套餐需填）' },
        ],
      },
    ],
  },
  {
    key: 'cliproxyapi',
    label: 'CLIProxyAPI',
    icon: <ApiOutlined />,
    sections: [
      {
        title: '管理面板',
        desc: '用于 CLIProxyAPI 管理页登录',
        fields: [
          { key: 'cliproxyapi_base_url', label: 'API URL', placeholder: 'http://127.0.0.1:8317' },
          { key: 'cliproxyapi_management_key', label: '管理口令', secret: true, placeholder: '默认 cliproxyapi' },
        ],
      },
    ],
  },
  {
    key: 'grok',
    label: 'Grok',
    icon: <ApiOutlined />,
    sections: [
      {
        title: 'grok2api',
        desc: '注册成功后自动导入到 grok2api 管理后台',
        fields: [
          { key: 'grok2api_url', label: 'API URL', placeholder: 'http://127.0.0.1:7860' },
          { key: 'grok2api_app_key', label: 'App Key', secret: true },
          { key: 'grok2api_pool', label: 'Token Pool', placeholder: 'ssoBasic 或 ssoSuper' },
          { key: 'grok2api_quota', label: 'Quota（可选）', placeholder: '留空按池默认值' },
        ],
      },
    ],
  },
  {
    key: 'kiro',
    label: 'Kiro',
    icon: <ApiOutlined />,
    sections: [
      {
        title: 'Kiro Account Manager',
        desc: '注册成功后自动写入 kiro-account-manager 的 accounts.json',
        fields: [
          {
            key: 'kiro_manager_path',
            label: 'accounts.json 路径（可选）',
            placeholder: '留空则自动使用系统默认路径',
          },
          {
            key: 'kiro_manager_exe',
            label: 'Kiro Manager 可执行文件（可选）',
            placeholder: '未安装 Rust 时可填写已安装的 KiroAccountManager.exe',
          },
        ],
      },
    ],
  },
  {
    key: 'integrations',
    label: '插件',
    icon: <ApiOutlined />,
    sections: [],
  },
  {
    key: 'security',
    label: '安全',
    icon: <LockOutlined />,
    sections: [],
  },
  {
    key: 'claude',
    label: 'Claude 导入',
    icon: <ApiOutlined />,
    sections: [],
  },
]

interface FieldConfig {
  key: string
  label: string
  placeholder?: string
  type?: 'select' | 'input' | 'boolean'
  secret?: boolean
  span?: 1 | 2
}

interface SectionConfig {
  title: string
  desc?: string
  fields: FieldConfig[]
  provider?: string
}

interface TabConfig {
  key: string
  label: string
  icon: React.ReactNode
  sections: SectionConfig[]
}

const EXTRA_PAYMENT_FIELDS: FieldConfig[] = [
  { key: 'payment_provider', label: 'Payment Provider Route', type: 'select' },
  { key: 'payment_promo_proxy_url', label: 'Japan/Promo Proxy URL', placeholder: 'socks5://jp-user:pass@host:port or http://host:port' },
  { key: 'payment_promo_proxy_geo', label: 'Promo Proxy Geo', type: 'select' },
  { key: 'payment_paypal_proxy_url', label: 'PayPal Dedicated Proxy URL', placeholder: 'Optional; ignored when promo proxy is set' },
  { key: 'payment_proxy_pool', label: 'Retry Proxy Pool', placeholder: 'Comma-separated proxies for retry/IP rotation' },
  { key: 'payment_max_retries', label: 'Payment Max Retries', placeholder: '2' },
  { key: 'payment_gopay_otp_retries', label: 'GoPay OTP Retries', placeholder: '2' },
  { key: 'payment_gojek_app_version', label: 'Gojek App Version', placeholder: 'Optional API header override' },
  { key: 'payment_android_avd_name', label: 'Android AVD Name', placeholder: 'Pixel_8_Play or your Google Play image AVD' },
  { key: 'payment_android_serial', label: 'Android Device Serial', placeholder: 'emulator-5554 (optional)' },
  { key: 'payment_android_headless', label: 'Android Emulator Mode', type: 'select' },
  { key: 'payment_android_gojek_apk', label: 'Gojek APK Path', placeholder: 'Optional local APK path' },
  { key: 'payment_android_gopay_apk', label: 'GoPay APK Path', placeholder: 'Optional local APK path' },
  { key: 'payment_android_adb_path', label: 'ADB Path', placeholder: 'Optional adb.exe path' },
  { key: 'payment_android_emulator_path', label: 'Emulator Path', placeholder: 'Optional emulator.exe path' },
]

function hasValue(value: unknown) {
  if (value === null || value === undefined) return false
  if (typeof value === 'string') return value.trim().length > 0
  return true
}

function PaymentUpgradeSummary({ form }: { form: any }) {
  const plan = String(Form.useWatch('payment_auto_plan', form) || '').trim()
  const plusFlowOrder = String(Form.useWatch('payment_plus_flow_order', form) || 'after_oauth').trim()
  const method = String(Form.useWatch('payment_method', form) || '').trim()
  const cardNumber = Form.useWatch('payment_card_number', form)
  const cardExpMonth = Form.useWatch('payment_card_exp_month', form)
  const cardExpYear = Form.useWatch('payment_card_exp_year', form)
  const cardCvc = Form.useWatch('payment_card_cvc', form)
  const billingName = Form.useWatch('payment_billing_name', form)
  const billingCountry = Form.useWatch('payment_billing_country', form)
  const paypalEmail = Form.useWatch('payment_paypal_email', form)
  const paypalPassword = Form.useWatch('payment_paypal_password', form)
  const gopayPhone = Form.useWatch('payment_gopay_phone', form)
  const gopayPin = Form.useWatch('payment_gopay_pin', form)
  const gopayAutoRegister = String(Form.useWatch('payment_gopay_auto_register', form) || '0').trim()
  const gopayOtpFile = Form.useWatch('payment_gopay_otp_file', form)
  const gopayOtpUrl = Form.useWatch('payment_gopay_otp_url', form)
  const teamWorkspaceName = Form.useWatch('payment_team_workspace_name', form)

  const planLabel =
    plan === 'plus'
      ? plusFlowOrder === 'before_oauth'
        ? '基础账号后先升级 Plus，再 OAuth'
        : '注册后自动升级 Plus'
      : plan === 'team'
        ? '注册后自动升级 Team'
        : '只注册，不自动升级'

  const methodLabel =
    method === 'card'
      ? '信用卡'
      : method === 'gopay'
        ? 'GoPay'
        : method === 'paypal'
          ? 'PayPal'
          : '未设置'

  const warnings: string[] = []
  if (plan) {
    if (!method) {
      warnings.push('已开启自动升级，但还没有选择支付方式。')
    }
    if (method === 'card') {
      const missing: string[] = []
      if (!hasValue(cardNumber)) missing.push('卡号')
      if (!hasValue(cardExpMonth)) missing.push('到期月')
      if (!hasValue(cardExpYear)) missing.push('到期年')
      if (!hasValue(cardCvc)) missing.push('CVC')
      if (!hasValue(billingName)) missing.push('持卡人姓名')
      if (!hasValue(billingCountry)) missing.push('账单国家')
      if (missing.length > 0) {
        warnings.push(`信用卡信息未填完整：${missing.join('、')}`)
      }
    }
    if (method === 'gopay') {
      const missing: string[] = []
      if (gopayAutoRegister !== '1') {
        if (!hasValue(gopayPhone)) missing.push('GoPay 手机号')
        if (!hasValue(gopayPin)) missing.push('GoPay PIN')
      }
      if (missing.length > 0) {
        warnings.push(`GoPay 信息未填完整：${missing.join('、')}`)
      }
      if (!hasValue(gopayOtpUrl) && !hasValue(gopayOtpFile)) {
        warnings.push('未填写 GoPay OTP URL/文件时，将回退使用 runtime/wa_relay/wa-otp.txt。')
      }
    }
    if (method === 'paypal') {
      const missing: string[] = []
      if (!hasValue(paypalEmail)) missing.push('PayPal 账号')
      if (!hasValue(paypalPassword)) missing.push('PayPal 密码')
      if (missing.length > 0) {
        warnings.push(`PayPal 信息未填完整：${missing.join('、')}`)
      }
    }
    if (plan === 'team' && !hasValue(teamWorkspaceName)) {
      warnings.push('Team 模式还没有填写 Workspace 名称。')
    }
    if (plan === 'team' && plusFlowOrder === 'before_oauth') {
      warnings.push('“OAuth 前升级”只对 Plus 生效；Team 仍会按原链路在 OAuth/Token 后升级。')
    }
  }

  const alertType = !plan ? 'info' : warnings.length > 0 ? 'warning' : 'success'

  return (
    <Alert
      type={alertType}
      showIcon
      style={{ marginBottom: 16 }}
      message={`当前模式：${planLabel}`}
      description={
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <Space wrap>
            <Tag color={!plan ? 'blue' : plan === 'plus' ? 'gold' : 'purple'}>{plan || 'register-only'}</Tag>
            {plan === 'plus' ? (
              <Tag color={plusFlowOrder === 'before_oauth' ? 'orange' : 'cyan'}>
                支付时机：{plusFlowOrder === 'before_oauth' ? 'OAuth 前' : 'OAuth 后'}
              </Tag>
            ) : null}
            <Tag color={method ? 'geekblue' : 'default'}>支付方式：{methodLabel}</Tag>
            {plan === 'team' ? (
              <Tag color={hasValue(teamWorkspaceName) ? 'success' : 'warning'}>
                Workspace：{hasValue(teamWorkspaceName) ? '已配置' : '未配置'}
              </Tag>
            ) : null}
            {method === 'gopay' ? (
              <Tag color={hasValue(gopayOtpUrl) || hasValue(gopayOtpFile) ? 'success' : 'processing'}>
                OTP：{hasValue(gopayOtpUrl) ? 'URL' : hasValue(gopayOtpFile) ? '文件' : 'WA Relay 默认文件'}
              </Tag>
            ) : null}
          </Space>
          {warnings.length > 0 ? (
            <div>
              {warnings.map((warning) => (
                <div key={warning} style={{ color: '#ad6800' }}>
                  {warning}
                </div>
              ))}
            </div>
          ) : (
            <Typography.Text type="secondary">
              {!plan ? '当前保存的是只注册模式，注册成功后不会自动升级 Plus 或 Team。' : '当前配置看起来可用于自动升级。'}
            </Typography.Text>
          )}
        </div>
      }
    />
  )
}

function formatResultText(data: unknown) {
  if (typeof data === 'string') return data
  try {
    return JSON.stringify(data, null, 2)
  } catch {
    return String(data)
  }
}

function normalizeDomainList(input: unknown): string[] {
  const items = Array.isArray(input) ? input : []
  const seen = new Set<string>()
  const domains: string[] = []
  for (const item of items) {
    const domain = String(item || '').trim().toLowerCase().replace(/^@/, '')
    if (!domain || seen.has(domain)) continue
    seen.add(domain)
    domains.push(domain)
  }
  return domains
}

function parseStoredDomainList(value: unknown): string[] {
  if (Array.isArray(value)) return normalizeDomainList(value)
  if (typeof value !== 'string') return []

  const text = value.trim()
  if (!text) return []

  try {
    const parsed = JSON.parse(text)
    if (Array.isArray(parsed)) {
      return normalizeDomainList(parsed)
    }
  } catch {}

  return normalizeDomainList(
    text
      .split('\n')
      .flatMap((line) => line.split(','))
      .map((item) => item.trim()),
  )
}

function ConfigField({ field }: { field: FieldConfig }) {
  const [showSecret, setShowSecret] = useState(false)
  const mergedField = { ...field, ...(FIELD_UI_OVERRIDES[field.key] || {}) }
  const options =
    SELECT_FIELDS[field.key] ||
    (SELECT_OPTION_OVERRIDES[field.key]
      ? Object.entries(SELECT_OPTION_OVERRIDES[field.key]).map(([value, label]) => ({ value, label }))
      : undefined)
  const isBooleanField = field.type === 'boolean'
  const helpText =
    field.key === 'default_executor'
      ? '仅对支持的平台生效；ChatGPT、Cursor、Grok、Kiro、Tavily、Trae 支持浏览器模式，OpenBlockLabs 仅支持纯协议。'
      : field.key === 'payment_plus_flow_order'
        ? '默认保持现有链路；选择 OAuth 前时，仅 Plus 会在基础账号创建完成后先走支付，再继续 OAuth/token 和上传。'
      : field.key === 'payment_card_py_path'
        ? '用于调用外部 CTF-pay/card.py。留空时会先读 CARD_PY_PATH，再尝试项目相邻目录和内置默认路径。'
      : field.key === 'payment_gopay_otp_file'
        ? '留空时，GoPay 会自动回退到 runtime/wa_relay/wa-otp.txt，适配内置 WhatsApp Relay。'
      : undefined

  return (
    <div className={`settings-field${mergedField.span === 2 ? ' settings-field--full' : ''}`}>
    <Form.Item
      label={mergedField.label}
      name={field.key}
      extra={helpText}
      valuePropName={isBooleanField ? 'checked' : undefined}
    >
      {options ? (
        <Select options={options} style={{ width: '100%' }} popupClassName="settings-select-dropdown" />
      ) : isBooleanField ? (
        <Switch checkedChildren="开启" unCheckedChildren="关闭" />
      ) : field.secret ? (
        <Input.Password
          placeholder={mergedField.placeholder}
          visibilityToggle={{
            visible: !showSecret,
            onVisibleChange: setShowSecret,
          }}
          iconRender={(visible) => (visible ? <EyeOutlined /> : <EyeInvisibleOutlined />)}
        />
      ) : (
        <Input placeholder={mergedField.placeholder} />
      )}
    </Form.Item>
    </div>
  )
}

function ConfigSection({ form, section }: { form: any; section: SectionConfig }) {
  const showPaymentSummary = section.fields.some((field) => field.key === 'payment_auto_plan')
  const allFields = showPaymentSummary ? [...section.fields, ...EXTRA_PAYMENT_FIELDS] : section.fields
  const sectionKey = getSectionKey(section)
  const sectionUi = SECTION_UI_OVERRIDES[sectionKey] || {}

  const paymentPrimaryFields = allFields.filter((field) =>
    [
      'payment_auto_plan',
      'payment_plus_flow_order',
      'payment_method',
      'payment_provider',
      'payment_skip_if_not_free',
      'payment_auto_cancel_after_subscribe',
      'payment_gopay_auto_register',
      'payment_checkout_ui_mode',
      'payment_billing_country',
    ].includes(field.key),
  )

  const paymentCardFields = allFields.filter((field) =>
    [
      'payment_card_number',
      'payment_card_exp_month',
      'payment_card_exp_year',
      'payment_card_cvc',
      'payment_billing_name',
      'payment_billing_address',
      'payment_billing_city',
      'payment_billing_state',
      'payment_billing_zip',
    ].includes(field.key),
  )

  const paymentGopayFields = allFields.filter((field) =>
    [
      'payment_gopay_phone',
      'payment_gopay_pin',
      'payment_gopay_otp_file',
      'payment_gopay_otp_url',
      'payment_gopay_sms_country',
      'payment_gopay_sms_service',
      'payment_gopay_otp_retries',
      'wa_relay_src_dir',
      'wa_relay_proxy_url',
      'payment_android_avd_name',
      'payment_android_serial',
      'payment_android_headless',
      'payment_android_gojek_apk',
      'payment_android_gopay_apk',
      'payment_android_adb_path',
      'payment_android_emulator_path',
      'payment_gojek_app_version',
    ].includes(field.key),
  )

  const paymentProxyFields = allFields.filter((field) =>
    [
      'payment_promo_proxy_url',
      'payment_promo_proxy_geo',
      'payment_paypal_proxy_url',
      'payment_proxy_pool',
      'payment_max_retries',
    ].includes(field.key),
  )

  const paymentAdvancedFields = allFields.filter(
    (field) =>
      ![
        ...paymentPrimaryFields,
        ...paymentCardFields,
        ...paymentGopayFields,
        ...paymentProxyFields,
      ].some((item) => item.key === field.key),
  )

  return (
    <Card
      id={`settings-section-${section.title}`}
      title={sectionUi.title || section.title}
      extra={(sectionUi.desc || section.desc) && <span style={{ fontSize: 12, color: '#7a8ba3' }}>{sectionUi.desc || section.desc}</span>}
      style={{ marginBottom: 16 }}
    >
      {showPaymentSummary ? <PaymentUpgradeSummary form={form} /> : null}
      {showPaymentSummary ? (
        <Collapse
          bordered={false}
          defaultActiveKey={['primary']}
          className="settings-collapse"
          items={[
            {
              key: 'primary',
              label: '支付策略',
              children: (
                <div className="settings-fields-grid">
                  {paymentPrimaryFields.map((field) => (
                    <ConfigField key={field.key} field={field} />
                  ))}
                </div>
              ),
            },
            {
              key: 'card',
              label: '信用卡与账单信息',
              children: (
                <div className="settings-fields-grid">
                  {paymentCardFields.map((field) => (
                    <ConfigField key={field.key} field={{ ...field, span: field.key === 'payment_card_number' ? 2 : field.span }} />
                  ))}
                </div>
              ),
            },
            {
              key: 'gopay',
              label: 'GoPay / Android',
              children: (
                <div className="settings-fields-grid">
                  {paymentGopayFields.map((field) => (
                    <ConfigField
                      key={field.key}
                      field={{
                        ...field,
                        span:
                          field.key.includes('_path') ||
                          field.key.includes('_url') ||
                          field.key === 'wa_relay_src_dir'
                            ? 2
                            : field.span,
                      }}
                    />
                  ))}
                </div>
              ),
            },
            {
              key: 'proxy',
              label: '代理与重试',
              children: (
                <div className="settings-fields-grid">
                  {paymentProxyFields.map((field) => (
                    <ConfigField
                      key={field.key}
                      field={{ ...field, span: field.key.includes('proxy') ? 2 : field.span }}
                    />
                  ))}
                </div>
              ),
            },
            {
              key: 'advanced',
              label: '高级项',
              children: (
                <div className="settings-fields-grid">
                  {paymentAdvancedFields.map((field) => (
                    <ConfigField
                      key={field.key}
                      field={{
                        ...field,
                        span:
                          field.key.includes('_url') ||
                          field.key.includes('_key') ||
                          field.key.includes('_path') ||
                          field.key.includes('_ids') ||
                          field.key.includes('_json')
                            ? 2
                            : field.span,
                      }}
                    />
                  ))}
                </div>
              ),
            },
          ]}
        />
      ) : (
        <div className="settings-fields-grid">
          {allFields.map((field) => (
            <ConfigField
              key={field.key}
              field={{
                ...field,
                span:
                  field.key.includes('_url') ||
                  field.key.includes('_path') ||
                  field.key.includes('_json') ||
                  field.key.includes('_addresses') ||
                  field.key.includes('_domains') ||
                  field.key.includes('_provider_ids') ||
                  field.key.includes('_group_ids')
                    ? 2
                    : field.span,
              }}
            />
          ))}
        </div>
      )}
    </Card>
  )
}

function _MailboxSectionsLegacy({ form, sections }: { form: any; sections: SectionConfig[] }) {
  const selectedProvider = Form.useWatch('mail_provider', form) || 'luckmail'
  const baseSections = sections.filter((section) => !section.provider)
  const providerSections = sections.filter((section) => section.provider)
  const activeProviderSection =
    providerSections.find((section) => section.provider === selectedProvider) || providerSections[0]
  const activeProviderKey = activeProviderSection ? getSectionKey(activeProviderSection) : ''
  const activeProviderUi = activeProviderKey ? SECTION_UI_OVERRIDES[activeProviderKey] || {} : {}

  return (
    <>
      {baseSections.map((section) => (
        <ConfigSection key={section.title} form={form} section={section} />
      ))}

      {activeProviderSection ? (
        <Card
          id={`settings-section-${activeProviderSection.title}`}
          title={activeProviderUi.title || activeProviderSection.title}
          extra={(activeProviderUi.desc || activeProviderSection.desc) && <span style={{ fontSize: 12, color: '#7a8ba3' }}>{activeProviderUi.desc || activeProviderSection.desc}</span>}
          style={{ marginBottom: 16 }}
        >
          {activeProviderSection.fields.length > 0 ? (
            <div className="settings-fields-grid">
              {activeProviderSection.fields.map((field) => <ConfigField key={field.key} field={field} />)}
            </div>
          ) : (
            <Typography.Text type="secondary">当前邮箱服务无需额外配置。</Typography.Text>
          )}
        </Card>
      ) : null}
    </>
  )
}

function UnifiedMailboxSections({ form, sections }: { form: any; sections: SectionConfig[] }) {
  const selectedProvider = Form.useWatch('mail_provider', form) || 'luckmail'
  const baseSections = sections.filter((section) => !section.provider)
  const providerSections = sections.filter((section) => section.provider)
  const selectorSection = baseSections.find((section) => section.fields.some((field) => field.key === 'mail_provider'))
  const extraSections = baseSections.filter(
    (section) =>
      section !== selectorSection &&
      !isSmsProviderSection(section) &&
      !isSmstomeSection(section) &&
      !isLegacyPaymentSection(section),
  )
  const activeProviderSection =
    providerSections.find((section) => section.provider === selectedProvider) || providerSections[0]
  const activeProviderKey = activeProviderSection ? getSectionKey(activeProviderSection) : ''
  const activeProviderUi = activeProviderKey ? SECTION_UI_OVERRIDES[activeProviderKey] || {} : {}

  return (
    <>
      {activeProviderSection ? (
        <Card
          id={`settings-section-${activeProviderSection.title}`}
          title={activeProviderUi.title || activeProviderSection.title}
          extra={
            <span style={{ fontSize: 12, color: '#7a8ba3' }}>
              {activeProviderUi.desc || activeProviderSection.desc || '选择服务后，这里会直接显示对应配置。'}
            </span>
          }
          style={{ marginBottom: 16 }}
        >
          {selectorSection ? (
            <div className="settings-provider-hero">
              <div className="settings-provider-hero__main">
                <div>
                  <Typography.Text strong>默认邮箱服务</Typography.Text>
                  <Typography.Paragraph type="secondary" style={{ margin: '6px 0 0' }}>
                    切换后，下方配置会立刻联动显示，不需要再往下滚动找对应服务。
                  </Typography.Paragraph>
                </div>
                <Tag color="blue">{activeProviderUi.title || activeProviderSection.title}</Tag>
              </div>
              <div className="settings-provider-hero__field">
                <ConfigField field={{ ...selectorSection.fields[0], label: '邮箱服务', span: 2 }} />
              </div>
            </div>
          ) : null}

          {activeProviderSection.fields.length > 0 ? (
            <div className="settings-fields-grid">
              {activeProviderSection.fields.map((field) => (
                <ConfigField key={field.key} field={field} />
              ))}
            </div>
          ) : (
            <Typography.Text type="secondary">当前邮箱服务无需额外配置。</Typography.Text>
          )}
        </Card>
      ) : null}

      {extraSections.map((section) => (
        <ConfigSection key={section.title} form={form} section={section} />
      ))}
    </>
  )
}

function ChatGPTPhoneSections({ form, sections }: { form: any; sections: SectionConfig[] }) {
  const smsSection = sections.find(isSmsProviderSection)
  const smstomeSection = sections.find(isSmstomeSection)
  const otherSections = sections.filter((section) => !isSmsProviderSection(section) && !isSmstomeSection(section))

  return (
    <>
      {smsSection || smstomeSection ? (
        <Card
          id="settings-section-chatgpt-phone"
          title="ChatGPT 手机验证"
          extra={<span style={{ fontSize: 12, color: '#7a8ba3' }}>把 add-phone 相关能力放在同一块里，少来回找配置。</span>}
          style={{ marginBottom: 16 }}
        >
          <div className="settings-inline-summary">
            <Typography.Text type="secondary">
              在线接码平台和 SMSToMe 都服务于 ChatGPT 的 add-phone，但来源不同。
              `SMS Provider` 负责在线买号接码，`SMSToMe` 负责号码池和轮询短信。
            </Typography.Text>
          </div>

          <div className="settings-dual-panels">
            {smsSection ? (
              <Card
                size="small"
                className="settings-subcard"
                title="在线接码平台"
                extra={<span style={{ fontSize: 12, color: '#7a8ba3' }}>SMSBOWER / 5SIM / HeroSMS</span>}
              >
                <div className="settings-fields-grid">
                  {smsSection.fields.map((field) => (
                    <ConfigField key={field.key} field={field} />
                  ))}
                </div>
              </Card>
            ) : null}

            {smstomeSection ? (
              <Card
                size="small"
                className="settings-subcard"
                title="SMSToMe 号码池"
                extra={<span style={{ fontSize: 12, color: '#7a8ba3' }}>Cookie 同步、号码池、短信轮询</span>}
              >
                <div className="settings-fields-grid">
                  {smstomeSection.fields.map((field) => (
                    <ConfigField key={field.key} field={field} />
                  ))}
                </div>
              </Card>
            ) : null}
          </div>
        </Card>
      ) : null}

      {otherSections.map((section) => (
        <ConfigSection key={section.title} form={form} section={section} />
      ))}
    </>
  )
}

function ChatGPTPhoneSectionsStacked({ form, sections }: { form: any; sections: SectionConfig[] }) {
  const smsSection = sections.find(isSmsProviderSection)
  const smstomeSection = sections.find(isSmstomeSection)
  const otherSections = sections.filter((section) => !isSmsProviderSection(section) && !isSmstomeSection(section))
  const phoneSource = Form.useWatch('chatgpt_phone_source', form) || 'smsbower'
  const phoneSourceField: FieldConfig = {
    key: 'chatgpt_phone_source',
    label: '接码服务',
    type: 'select',
    span: 2,
  }

  useEffect(() => {
    if (!['smsbower', '5sim', 'herosms'].includes(phoneSource)) return
    if (form.getFieldValue('sms_provider') === phoneSource) return
    form.setFieldValue('sms_provider', phoneSource)
  }, [form, phoneSource])

  return (
    <>
      {smsSection || smstomeSection ? (
        <Card
          id="settings-section-chatgpt-phone"
          title="ChatGPT 手机验证"
          extra={<span style={{ fontSize: 12, color: '#7a8ba3' }}>一个下拉统一切换，不再重复选两次接码平台。</span>}
          style={{ marginBottom: 16 }}
        >
          <div className="settings-inline-summary">
            <Typography.Text type="secondary">
              和邮箱服务一样，先选手机号来源，下面只显示当前方案需要填写的配置。
            </Typography.Text>
          </div>

          <div className="settings-stack-panel">
            <div className="settings-stack-panel__header">
              <div>
                <Typography.Text strong>手机号来源</Typography.Text>
                <Typography.Paragraph type="secondary" style={{ margin: '6px 0 0' }}>
                  推荐默认用 SMSBOWER。只有你已经有现成的 SMSToMe Cookie 或号码池时，再切到 SMSToMe。
                </Typography.Paragraph>
              </div>
              <Tag color={phoneSource === 'smstome' ? 'gold' : 'blue'}>
                {phoneSource === 'smsbower' ? '推荐默认' : phoneSource === 'smstome' ? '号码池模式' : '在线接码'}
              </Tag>
            </div>
            <div className="settings-fields-grid">
              <ConfigField field={phoneSourceField} />
            </div>
          </div>

          {phoneSource === 'smstome' ? (
            smstomeSection ? (
              <div className="settings-stack-panel">
                <div className="settings-stack-panel__header">
                  <div>
                    <Typography.Text strong>SMSToMe 配置</Typography.Text>
                    <Typography.Paragraph type="secondary" style={{ margin: '6px 0 0' }}>
                      适合你已经有 SMSToMe cookie 或号码池资源的情况。没有的话建议切回 SMSBOWER。
                    </Typography.Paragraph>
                  </div>
                  <Tag color="gold">号码池</Tag>
                </div>
                <div className="settings-fields-grid">
                  {smstomeSection.fields.map((field) => (
                    <ConfigField
                      key={field.key}
                      field={{
                        ...field,
                        span: field.key === 'smstome_cookie' ? 2 : field.span,
                      }}
                    />
                  ))}
                </div>
              </div>
            ) : null
          ) : smsSection ? (
            <div className="settings-stack-panel">
                <div className="settings-stack-panel__header">
                  <div>
                    <Typography.Text strong>在线接码配置</Typography.Text>
                    <Typography.Paragraph type="secondary" style={{ margin: '6px 0 0' }}>
                      默认推荐 SMSBOWER。只有你明确使用 5SIM 或 HeroSMS 时，再切换并填写对应 key。
                    </Typography.Paragraph>
                  </div>
                <Tag color="blue">{phoneSource === 'smsbower' ? 'SMSBOWER' : phoneSource === '5sim' ? '5SIM' : 'HeroSMS'}</Tag>
              </div>
              <div className="settings-fields-grid">
                {smsSection.fields
                  .filter((field) => {
                    if (field.key === 'sms_provider') return false
                    if (field.key === 'smsbower_api_key') return phoneSource === 'smsbower'
                    if (field.key === 'sim5_api_key') return phoneSource === '5sim'
                    if (field.key === 'herosms_api_key') return phoneSource === 'herosms'
                    return true
                  })
                  .map((field) => (
                    <ConfigField
                      key={field.key}
                      field={{
                        ...field,
                        span:
                          field.key.includes('_provider_ids') ||
                          field.key.includes('_except_provider_ids')
                            ? 2
                            : field.span,
                        label:
                          field.key === 'smsbower_api_key'
                            ? 'SMSBOWER API Key'
                            : field.key === 'sim5_api_key'
                              ? '5SIM API Key'
                                : field.key === 'herosms_api_key'
                                  ? 'HeroSMS API Key'
                                  : field.label,
                      }}
                    />
                  ))}
              </div>
            </div>
          ) : null}
        </Card>
      ) : null}

      {otherSections.map((section) => (
        <ConfigSection key={section.title} form={form} section={section} />
      ))}
    </>
  )
}

void ChatGPTPhoneSections

function ChatGPTFlowSummary({ form }: { form: any }) {
  const executor = Form.useWatch('default_executor', form) || 'protocol'
  const captcha = Form.useWatch('default_captcha_solver', form) || 'yescaptcha'
  const phoneSource = Form.useWatch('chatgpt_phone_source', form) || 'smsbower'

  const executorLabel =
    SELECT_OPTION_OVERRIDES.default_executor?.[executor] || executor
  const captchaLabel =
    SELECT_OPTION_OVERRIDES.default_captcha_solver?.[captcha] || captcha
  const phoneLabel =
    phoneSource === 'smsbower'
      ? 'SMSBOWER'
      : phoneSource === 'smstome'
        ? 'SMSToMe'
        : phoneSource === '5sim'
          ? '5SIM'
          : phoneSource === 'herosms'
            ? 'HeroSMS'
            : phoneSource

  return (
    <Card className="settings-flow-summary" bordered={false}>
      <div className="settings-flow-summary__head">
        <div>
          <Typography.Text strong>ChatGPT 注册流程总览</Typography.Text>
          <Typography.Paragraph type="secondary" style={{ margin: '6px 0 0' }}>
            这里汇总最影响成功率的三段配置，下面再改细节，避免来回找入口。
          </Typography.Paragraph>
        </div>
        <Tag color="cyan">推荐先检查这里</Tag>
      </div>
      <div className="settings-flow-summary__grid">
        <div className="settings-flow-summary__item">
          <span>执行方式</span>
          <strong>{executorLabel}</strong>
        </div>
        <div className="settings-flow-summary__item">
          <span>验证码</span>
          <strong>{captchaLabel}</strong>
        </div>
        <div className="settings-flow-summary__item">
          <span>手机接码</span>
          <strong>{phoneLabel}</strong>
        </div>
      </div>
    </Card>
  )
}

function ChatGPTManualPlusSection() {
  return (
    <Card
      id="settings-section-chatgpt-plus-manual"
      title="Plus 手动升级"
      extra={<span style={{ fontSize: 12, color: '#7a8ba3' }}>自动 Plus / GoPay 流程已下线，保留账号直取长链</span>}
      style={{ marginBottom: 16 }}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <Alert
          type="info"
          showIcon
          message="现在只保留手动支付，但长链由系统直接代取"
          description="请在账号页右侧操作菜单中使用“获取 Plus 长链”。系统会直接读取该账号的 access_token 生成长链，然后你手动打开支付即可。"
        />
      </div>
    </Card>
  )
}

function CFWorkerDomainPoolSection({ form }: { form: any }) {
  const watchedDomains = Form.useWatch('cfworker_domains', form) || []
  const watchedEnabledDomains = Form.useWatch('cfworker_enabled_domains', form) || []
  const normalizedDomains = normalizeDomainList(watchedDomains)
  const enabledDomains = normalizeDomainList(watchedEnabledDomains).filter((domain) => normalizedDomains.includes(domain))

  const updateEnabledDomains = (nextDomains: string[]) => {
    form.setFieldValue('cfworker_enabled_domains', normalizeDomainList(nextDomains))
  }

  const toggleEnabledDomain = (domain: string, checked: boolean) => {
    if (checked) {
      updateEnabledDomains([...enabledDomains, domain])
      return
    }
    updateEnabledDomains(enabledDomains.filter((item) => item !== domain))
  }

  return (
    <Card
      title="CF Worker 域名池"
      extra={<span style={{ fontSize: 12, color: '#7a8ba3' }}>注册时会从已启用域名中随机选择一个</span>}
      style={{ marginBottom: 16 }}
    >
      <Form.List name="cfworker_domains">
        {(fields, { add, remove }) => (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {fields.map((field) => (
              <Space key={field.key} align="start" style={{ display: 'flex' }}>
                <Form.Item
                  {...field}
                  label={field.name === 0 ? '全部域名' : ''}
                  style={{ flex: 1, marginBottom: 0 }}
                  rules={[
                    {
                      validator: async (_, value) => {
                        if (!String(value || '').trim()) {
                          throw new Error('请输入域名')
                        }
                      },
                    },
                  ]}
                >
                  <Input placeholder="example.com" />
                </Form.Item>
                <Button
                  danger
                  onClick={() => {
                    const currentDomains = Array.isArray(form.getFieldValue('cfworker_domains'))
                      ? [...form.getFieldValue('cfworker_domains')]
                      : []
                    const removedDomain = String(currentDomains[field.name] || '').trim().toLowerCase().replace(/^@/, '')
                    remove(field.name)
                    if (!removedDomain) return
                    const enabledDomains = normalizeDomainList(form.getFieldValue('cfworker_enabled_domains'))
                    form.setFieldValue(
                      'cfworker_enabled_domains',
                      enabledDomains.filter((domain) => domain !== removedDomain),
                    )
                  }}
                >
                  删除
                </Button>
              </Space>
            ))}
            {fields.length === 0 ? (
              <Typography.Text type="secondary">还没有配置域名。添加后即可在下方选择启用项。</Typography.Text>
            ) : null}
            <Button type="dashed" onClick={() => add('')} icon={<PlusOutlined />} block>
              添加域名
            </Button>
          </div>
        )}
      </Form.List>

      <Form.Item name="cfworker_enabled_domains" hidden>
        <Select mode="multiple" options={normalizedDomains.map((domain) => ({ label: domain, value: domain }))} />
      </Form.Item>

      <div style={{ marginTop: 16 }}>
        <div style={{ marginBottom: 8, fontWeight: 500 }}>已启用域名</div>
        {enabledDomains.length > 0 ? (
          <Space wrap>
            {enabledDomains.map((domain) => (
              <Tag
                key={domain}
                color="blue"
                closable
                onClose={(event) => {
                  event.preventDefault()
                  updateEnabledDomains(enabledDomains.filter((item) => item !== domain))
                }}
              >
                {domain}
              </Tag>
            ))}
          </Space>
        ) : (
          <Typography.Text type="secondary">暂无启用域名，点击下方域名即可启用。</Typography.Text>
        )}
      </div>

      <div style={{ marginTop: 16 }}>
        <div style={{ marginBottom: 8, fontWeight: 500 }}>点击切换启用状态</div>
        {normalizedDomains.length > 0 ? (
          <Space wrap>
            {normalizedDomains.map((domain) => (
              <Tag.CheckableTag
                key={domain}
                checked={enabledDomains.includes(domain)}
                onChange={(checked) => toggleEnabledDomain(domain, checked)}
              >
                {domain}
              </Tag.CheckableTag>
            ))}
          </Space>
        ) : (
          <Typography.Text type="secondary">请先在上方添加域名。</Typography.Text>
        )}
      </div>
      <Typography.Text type="secondary" style={{ display: 'block', marginTop: 12 }}>
        仅已启用域名会参与注册；点击已启用标签可直接移除。
      </Typography.Text>
    </Card>
  )
}

function _SolverStatusLegacy() {
  const [running, setRunning] = useState<boolean | null>(null)

  const checkSolver = async () => {
    try {
      const d = await apiFetch('/solver/status')
      setRunning(d.running)
    } catch {
      setRunning(false)
    }
  }

  const restartSolver = async () => {
    await apiFetch('/solver/restart', { method: 'POST' })
    setRunning(null)
    setTimeout(checkSolver, 2000)
  }

  useEffect(() => {
    checkSolver()
    const timer = window.setInterval(checkSolver, 5000)
    return () => window.clearInterval(timer)
  }, [])

  return (
    <Card title="Turnstile Solver" size="small" style={{ marginBottom: 16 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 12,
          flexWrap: 'wrap',
        }}
      >
        <Space size={8}>
          {running === null ? (
            <SyncOutlined spin style={{ color: '#7a8ba3' }} />
          ) : running ? (
            <CheckCircleOutlined style={{ color: '#10b981' }} />
          ) : (
            <CloseCircleOutlined style={{ color: '#ef4444' }} />
          )}
          <span style={{ color: running ? '#10b981' : '#7a8ba3', fontWeight: 500 }}>
            {running === null ? '检测中' : running ? '运行中' : '未运行'}
          </span>
        </Space>
        <Button size="small" onClick={restartSolver}>
          重启 Solver
        </Button>
      </div>
    </Card>
  )
}

function SolverStatusCompact() {
  const [running, setRunning] = useState<boolean | null>(null)

  const checkSolver = async () => {
    try {
      const d = await apiFetch('/solver/status')
      setRunning(d.running)
    } catch {
      setRunning(false)
    }
  }

  const restartSolver = async () => {
    await apiFetch('/solver/restart', { method: 'POST' })
    setRunning(null)
    setTimeout(checkSolver, 2000)
  }

  useEffect(() => {
    checkSolver()
    const timer = window.setInterval(checkSolver, 5000)
    return () => window.clearInterval(timer)
  }, [])

  return (
    <Card title="Turnstile Solver" size="small" style={{ marginBottom: 16 }}>
      <div className="settings-status-card">
        <div className="settings-status-card__main">
          <Space size={10}>
            {running === null ? (
              <SyncOutlined spin style={{ color: '#7a8ba3' }} />
            ) : running ? (
              <CheckCircleOutlined style={{ color: '#10b981' }} />
            ) : (
              <CloseCircleOutlined style={{ color: '#ef4444' }} />
            )}
            <div>
              <div className="settings-status-card__title">
                {running === null ? '正在检测状态' : running ? 'Solver 运行中' : 'Solver 未运行'}
              </div>
              <Typography.Text type="secondary">
                {running
                  ? 'Turnstile 验证可直接走本地求解。'
                  : '建议先重启 Solver，避免任务卡在验证码阶段。'}
              </Typography.Text>
            </div>
          </Space>
          <Space wrap>
            <Tag color={running ? 'success' : running === null ? 'processing' : 'error'}>
              {running === null ? '检测中' : running ? '在线' : '离线'}
            </Tag>
            <Button size="small" onClick={restartSolver}>
              重启 Solver
            </Button>
          </Space>
        </div>
      </div>
    </Card>
  )
}

void _MailboxSectionsLegacy
void _SolverStatusLegacy

function WaRelayStatus() {
  const [info, setInfo] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [pairingPhone, setPairingPhone] = useState('')

  const refresh = async () => {
    try {
      const d = await apiFetch('/wa_relay/status')
      setInfo(d)
    } catch {
      setInfo({ process_running: false, logged_in: false })
    }
  }

  const restart = async (login_mode: 'qr' | 'pairing' = 'qr') => {
    setLoading(true)
    try {
      const body: any = { login_mode }
      if (login_mode === 'pairing' && pairingPhone) {
        body.pairing_phone = pairingPhone.replace(/\D/g, '')
      }
      await apiFetch('/wa_relay/restart', { method: 'POST', body: JSON.stringify(body) })
      message.success('已重启 WhatsApp Relay，请等待几秒...')
      setTimeout(refresh, 3000)
    } finally {
      setLoading(false)
    }
  }

  const logout = async () => {
    Modal.confirm({
      title: '确认注销 WhatsApp？',
      content: '将清除 session，需要重新扫码或配对登录',
      onOk: async () => {
        await apiFetch('/wa_relay/logout', { method: 'POST' })
        message.success('已注销，正在重启 Relay...')
        setTimeout(refresh, 3000)
      },
    })
  }

  useEffect(() => {
    refresh()
    const timer = window.setInterval(refresh, 3000)
    return () => window.clearInterval(timer)
  }, [])

  if (!info) {
    return null
  }

  const status = String(info.status || '').toLowerCase()
  const connected = !!info.logged_in
  const running = !!info.process_running
  const showQR = !connected && (!!info.qr_data_url || !!info.qr_text)
  const showPairing = !connected && !!info.pairing_code
  const latestOtp = info.latest_otp || ''
  const latestOtpTime = info.latest_otp_time
    ? new Date(Number(info.latest_otp_time) * 1000).toLocaleString()
    : ''
  const relayError = String(info.error || '')
  const relayReason = String(info.reason || '')
  const relayConnectionTimedOut = /handshake|timed out|connection closed|websocket/i.test(`${relayError} ${relayReason}`)

  return (
    <Card
      title="📱 WhatsApp Relay（GoPay OTP 全自动）"
      size="small"
      style={{ marginBottom: 16 }}
      extra={
        <Space size={6}>
          {connected ? (
            <CheckCircleOutlined style={{ color: '#10b981' }} />
          ) : running ? (
            <SyncOutlined spin style={{ color: '#3b82f6' }} />
          ) : (
            <CloseCircleOutlined style={{ color: '#ef4444' }} />
          )}
          <span style={{ fontWeight: 500, color: connected ? '#10b981' : running ? '#3b82f6' : '#ef4444' }}>
            {connected ? '已登录' : status || (running ? '启动中' : '未运行')}
          </span>
        </Space>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {!info.node_available && (
          <Alert type="error" message="未找到 Node.js" description="WhatsApp Relay 需要 Node.js v18+。安装后重启后端或点击重启 Relay。" />
        )}

        {info.node_available && info.node_path && (
          <Alert type="success" message="Node.js 已找到" description={`当前路径: ${info.node_path}`} />
        )}

        {info.node_available && !info.relay_src_exists && (
          <Alert
            type="error"
            message="WhatsApp Relay 源码目录不存在"
            description={`当前目录: ${info.relay_src_dir || '未配置'}。请在自动支付配置中设置 WhatsApp Relay 源码目录，或设置 WA_RELAY_SRC_DIR。`}
          />
        )}

        {showQR && (
          <Alert
            type="warning"
            message="请用 WhatsApp 扫描二维码登录"
            description={
              <div style={{ marginTop: 8, textAlign: 'center' }}>
                {info.qr_data_url ? (
                  <img
                    src={info.qr_data_url}
                    alt="WhatsApp QR"
                    style={{ width: 240, height: 240, background: '#fff', padding: 8, borderRadius: 8 }}
                  />
                ) : (
                  <QRCode value={String(info.qr_text || '')} size={240} />
                )}
                <div style={{ marginTop: 8, color: '#7a8ba3', fontSize: 12 }}>
                  WhatsApp → 设置 → 已链接的设备 → 链接设备
                </div>
              </div>
            }
          />
        )}

        {showPairing && (
          <Alert
            type="info"
            message="请在 WhatsApp 输入配对码"
            description={
              <div style={{ marginTop: 8 }}>
                <div
                  style={{
                    fontSize: 32,
                    fontWeight: 'bold',
                    letterSpacing: 4,
                    textAlign: 'center',
                    fontFamily: 'monospace',
                    background: '#fff',
                    padding: 12,
                    borderRadius: 8,
                  }}
                >
                  {info.pairing_code}
                </div>
                <div style={{ marginTop: 8, color: '#7a8ba3', fontSize: 12 }}>
                  WhatsApp → 设置 → 已链接的设备 → 链接设备 → 用电话号码链接
                </div>
              </div>
            }
          />
        )}

        {connected && latestOtp && (
          <Alert
            type="success"
            message={`最近 OTP: ${latestOtp}`}
            description={`捕获时间: ${latestOtpTime}`}
          />
        )}

        {info.error && (
          <Alert
            type="error"
            message="Relay 错误"
            description={
              relayConnectionTimedOut
                ? `${relayError}。这是 WhatsApp WebSocket 连接失败，请在自动支付配置里填写 WhatsApp Relay 代理，例如 http://127.0.0.1:7897，保存后点重启（QR 模式）。`
                : relayError
            }
          />
        )}

        <Space wrap>
          <Button size="small" loading={loading} onClick={() => restart('qr')}>
            🔄 重启（QR 模式）
          </Button>
          <Input
            size="small"
            placeholder="手机号 如 8615870862693"
            value={pairingPhone}
            onChange={(e) => setPairingPhone(e.target.value)}
            style={{ width: 200 }}
          />
          <Button size="small" loading={loading} onClick={() => restart('pairing')} disabled={!pairingPhone}>
            🔢 重启（配对码模式）
          </Button>
          {connected && (
            <Button size="small" danger onClick={logout}>
              注销 WhatsApp
            </Button>
          )}
        </Space>

        <div style={{ fontSize: 12, color: '#7a8ba3' }}>
          OTP 文件: <code>{info.otp_file}</code>
        </div>
      </div>
    </Card>
  )
}

void WaRelayStatus

function ClaudeImportPanel() {
  const [mode, setMode] = useState<'session_key' | 'tokens'>('session_key')
  const [sessionKey, setSessionKey] = useState('')
  const [accessToken, setAccessToken] = useState('')
  const [refreshToken, setRefreshToken] = useState('')
  const [emailAddress, setEmailAddress] = useState('')
  const [expiresAt, setExpiresAt] = useState('')
  const [orgUuid, setOrgUuid] = useState('')
  const [accountUuid, setAccountUuid] = useState('')
  const [importName, setImportName] = useState('')
  const [importing, setImporting] = useState(false)
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null)

  const doImport = async () => {
    setImporting(true)
    setResult(null)
    try {
      let data: any
      if (mode === 'session_key') {
        if (!sessionKey.trim()) {
          message.warning('请输入 sessionKey')
          setImporting(false)
          return
        }
        data = await apiFetch('/claude/import-from-session-key', {
          method: 'POST',
          body: JSON.stringify({
            session_key: sessionKey.trim(),
            name: importName.trim() || undefined,
          }),
        })
      } else {
        if (!accessToken.trim() || !refreshToken.trim()) {
          message.warning('请输入 access_token 和 refresh_token')
          setImporting(false)
          return
        }
        data = await apiFetch('/claude/import-from-tokens', {
          method: 'POST',
          body: JSON.stringify({
            access_token: accessToken.trim(),
            refresh_token: refreshToken.trim(),
            email_address: emailAddress.trim(),
            expires_at: expiresAt.trim() ? parseInt(expiresAt.trim(), 10) : undefined,
            org_uuid: orgUuid.trim(),
            account_uuid: accountUuid.trim(),
            name: importName.trim() || undefined,
          }),
        })
      }
      setResult({ ok: data.ok, message: data.message || '未知结果' })
      if (data.ok) {
        message.success(data.message || '导入成功')
      } else {
        message.error(data.message || '导入失败')
      }
    } catch (e: any) {
      setResult({ ok: false, message: e?.message || '导入异常' })
      message.error(e?.message || '导入异常')
    } finally {
      setImporting(false)
    }
  }

  return (
    <div className="page-container">
      <Card title="Claude 账号导入到 Sub2API">
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <div>
            <Typography.Text strong style={{ marginRight: 12 }}>导入模式</Typography.Text>
            <Segmented
              value={mode}
              onChange={(v) => setMode(v as 'session_key' | 'tokens')}
              options={[
                { label: 'Session Key（Cookie 交换）', value: 'session_key' },
                { label: 'OAuth Token（直接导入）', value: 'tokens' },
              ]}
            />
          </div>

          {mode === 'session_key' ? (
            <>
              <div>
                <Typography.Text strong style={{ display: 'block', marginBottom: 4 }}>Session Key</Typography.Text>
                <Input.TextArea
                  value={sessionKey}
                  onChange={(e) => setSessionKey(e.target.value)}
                  placeholder="sk-ant-sid01-..."
                  rows={4}
                  autoSize={{ minRows: 2, maxRows: 6 }}
                />
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  从浏览器 DevTools → Application → Cookies → claude.ai → sessionKey 获取
                </Typography.Text>
              </div>
            </>
          ) : (
            <>
              <div>
                <Typography.Text strong style={{ display: 'block', marginBottom: 4 }}>Access Token</Typography.Text>
                <Input.Password
                  value={accessToken}
                  onChange={(e) => setAccessToken(e.target.value)}
                  placeholder="sk-ant-api03-..."
                />
              </div>
              <div>
                <Typography.Text strong style={{ display: 'block', marginBottom: 4 }}>Refresh Token</Typography.Text>
                <Input.Password
                  value={refreshToken}
                  onChange={(e) => setRefreshToken(e.target.value)}
                  placeholder="sk-ant-api03-..."
                />
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div>
                  <Typography.Text strong style={{ display: 'block', marginBottom: 4 }}>Email Address</Typography.Text>
                  <Input
                    value={emailAddress}
                    onChange={(e) => setEmailAddress(e.target.value)}
                    placeholder="user@example.com"
                  />
                </div>
                <div>
                  <Typography.Text strong style={{ display: 'block', marginBottom: 4 }}>Expires At (Unix)</Typography.Text>
                  <Input
                    value={expiresAt}
                    onChange={(e) => setExpiresAt(e.target.value)}
                    placeholder="1715800000"
                  />
                </div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div>
                  <Typography.Text strong style={{ display: 'block', marginBottom: 4 }}>Org UUID（可选）</Typography.Text>
                  <Input
                    value={orgUuid}
                    onChange={(e) => setOrgUuid(e.target.value)}
                    placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                  />
                </div>
                <div>
                  <Typography.Text strong style={{ display: 'block', marginBottom: 4 }}>Account UUID（可选）</Typography.Text>
                  <Input
                    value={accountUuid}
                    onChange={(e) => setAccountUuid(e.target.value)}
                    placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                  />
                </div>
              </div>
            </>
          )}

          <div>
            <Typography.Text strong style={{ display: 'block', marginBottom: 4 }}>账号名称（可选）</Typography.Text>
            <Input
              value={importName}
              onChange={(e) => setImportName(e.target.value)}
              placeholder="留空则自动使用邮箱地址"
            />
          </div>

          <Button type="primary" onClick={doImport} loading={importing}>
            导入到 Sub2API
          </Button>

          {result && (
            <Alert
              type={result.ok ? 'success' : 'error'}
              message={result.ok ? '成功' : '失败'}
              description={result.message}
              closable
            />
          )}
        </Space>
      </Card>

      <Card title="查询远端账号">
        <Space direction="vertical" style={{ width: '100%' }}>
          <div>
            <Typography.Text strong style={{ display: 'block', marginBottom: 4 }}>Email</Typography.Text>
            <Input.Search
              placeholder="输入邮箱地址查询 sub2api 中是否已存在"
              enterButton="查询"
              onSearch={async (value) => {
                try {
                  const data = await apiFetch(`/claude/query-account?email=${encodeURIComponent(value.trim())}`)
                  if (data?.found) {
                    message.success(`远端已存在 (ID: ${data.remote_account_id})`)
                  } else if (data?.ok) {
                    message.info('远端未发现，可以导入')
                  } else {
                    message.warning(data?.message || '查询失败')
                  }
                } catch (e: any) {
                  message.error(e?.message || '查询异常')
                }
              }}
            />
          </div>
        </Space>
      </Card>

      <Card title="说明">
        <Typography.Paragraph style={{ fontSize: 13, color: '#6b7280', margin: 0 }}>
          <strong>前置条件：</strong>请在「CLIProxyAPI / Sub2API」Tab 中先配置好
          <code> sub2api_api_url </code>和<code> sub2api_api_key </code>。
          <br />
          <strong>Session Key 模式：</strong>将 Claude 登录后的
          <code> sessionKey </code>Cookie 通过 Sub2API 的 CookieAuth 接口自动换取 OAuth Token 后创建账号。
          <br />
          <strong>OAuth Token 模式：</strong>直接使用已有的 access_token + refresh_token 创建账号，
          不经过 CookieAuth 交换。
          <br />
          导入的账号在 Sub2API 中显示为 <code>platform=anthropic</code>、<code>type=oauth</code>。
        </Typography.Paragraph>
      </Card>
    </div>
  )
}

function IntegrationsPanel() {
  const [items, setItems] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState('')
  const [resultModal, setResultModal] = useState({
    open: false,
    title: '',
    ok: true,
    content: '',
  })

  const showResultModal = (title: string, data: unknown, ok = true) => {
    setResultModal({
      open: true,
      title,
      ok,
      content: formatResultText(data),
    })
  }

  const load = async () => {
    setLoading(true)
    try {
      const d = await apiFetch('/integrations/services')
      setItems(d.items || [])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    const timer = window.setInterval(load, 5000)
    return () => window.clearInterval(timer)
  }, [])

  const doAction = async (key: string, request: Promise<any>) => {
    setBusy(key)
    try {
      const result = await request
      await load()
      message.success('操作完成')
      showResultModal('操作结果', result, true)
    } catch (e: any) {
      message.error(e?.message || '操作失败')
      showResultModal('操作结果', e?.message || e || '操作失败', false)
      await load()
    } finally {
      setBusy('')
    }
  }

  const backfill = async (platforms: string[], label: string, busyKey: string) => {
    setBusy(busyKey)
    try {
      const d = await apiFetch('/integrations/backfill', {
        method: 'POST',
        body: JSON.stringify({ platforms }),
      })
      message.success(`${label} 回填完成：成功 ${d.success} / ${d.total}`)
      showResultModal(`${label} 回填结果`, d, true)
    } catch (e: any) {
      message.error(e?.message || `${label} 回填失败`)
      showResultModal(`${label} 回填结果`, e?.message || e || `${label} 回填失败`, false)
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="page-container">
      <Modal
        open={resultModal.open}
        title={resultModal.title}
        onCancel={() => setResultModal((v) => ({ ...v, open: false }))}
        onOk={() => setResultModal((v) => ({ ...v, open: false }))}
        width={760}
      >
        <Typography.Paragraph style={{ marginBottom: 8, color: resultModal.ok ? '#10b981' : '#ef4444' }}>
          {resultModal.ok ? '操作已完成。' : '操作失败。'}
        </Typography.Paragraph>
        <pre
          style={{
            margin: 0,
            maxHeight: 420,
            overflow: 'auto',
            padding: 12,
            borderRadius: 8,
            background: 'rgba(127,127,127,0.08)',
            fontSize: 12,
            lineHeight: 1.5,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {resultModal.content}
        </pre>
      </Modal>

      <Card title="批量操作">
        <Space wrap>
          <Button loading={busy === 'start-all'} onClick={() => doAction('start-all', apiFetch('/integrations/services/start-all', { method: 'POST' }))}>
            启动全部（已安装）
          </Button>
          <Button loading={busy === 'stop-all'} onClick={() => doAction('stop-all', apiFetch('/integrations/services/stop-all', { method: 'POST' }))}>
            停止全部
          </Button>
          <Button loading={loading} onClick={load}>
            刷新状态
          </Button>
        </Space>
      </Card>

      {items.map((item) => (
        <Card key={item.name} title={item.label}>
          <Space direction="vertical" style={{ width: '100%' }}>
            <div>
              状态：
              <Tag color={item.running ? 'green' : 'default'} style={{ marginLeft: 8 }}>
                {item.running ? '运行中' : '未运行'}
              </Tag>
              <Tag color={item.repo_exists ? 'blue' : 'orange'} style={{ marginLeft: 8 }}>
                {item.repo_exists ? '已安装' : '未安装'}
              </Tag>
              {item.pid ? <span style={{ marginLeft: 8 }}>PID: {item.pid}</span> : null}
            </div>
            <div>插件目录：<Typography.Text copyable>{item.repo_path}</Typography.Text></div>
            {item.url ? <div>地址：<Typography.Text copyable>{item.url}</Typography.Text></div> : null}
            {item.management_url ? <div>管理页：<Typography.Text copyable>{item.management_url}</Typography.Text></div> : null}
            {item.management_key ? <div>登录口令：<Typography.Text copyable>{item.management_key}</Typography.Text></div> : null}
            <div>日志：<Typography.Text copyable>{item.log_path}</Typography.Text></div>
            {item.last_error ? <div style={{ color: '#ef4444' }}>最近错误：{item.last_error}</div> : null}
            <Space wrap>
              {item.management_url ? (
                <Button onClick={() => window.open(item.management_url, '_blank')}>
                  打开管理页
                </Button>
              ) : null}
              {!item.repo_exists ? (
                <Button
                  type="primary"
                  loading={busy === `install-${item.name}`}
                  onClick={() => doAction(`install-${item.name}`, apiFetch(`/integrations/services/${item.name}/install`, { method: 'POST' }))}
                >
                  安装
                </Button>
              ) : null}
              <Button
                loading={busy === `start-${item.name}`}
                disabled={!item.repo_exists}
                onClick={() => doAction(`start-${item.name}`, apiFetch(`/integrations/services/${item.name}/start`, { method: 'POST' }))}
              >
                启动
              </Button>
              <Button
                loading={busy === `stop-${item.name}`}
                onClick={() => doAction(`stop-${item.name}`, apiFetch(`/integrations/services/${item.name}/stop`, { method: 'POST' }))}
              >
                停止
              </Button>
              {item.name === 'grok2api' ? (
                <Button
                  loading={busy === 'backfill-grok'}
                  onClick={() => backfill(['grok'], 'Grok', 'backfill-grok')}
                >
                  回填现有 Grok 账号
                </Button>
              ) : null}
              {item.name === 'kiro-manager' ? (
                <Button
                  loading={busy === 'backfill-kiro'}
                  onClick={() => backfill(['kiro'], 'Kiro', 'backfill-kiro')}
                >
                  回填现有 Kiro 账号
                </Button>
              ) : null}
            </Space>
          </Space>
        </Card>
      ))}
    </div>
  )
}

type TotpSetupState = 'idle' | 'setup'

function SecurityPanel() {
  const { message: msg } = App.useApp()
  const [status, setStatus] = useState<{ has_password: boolean; has_totp: boolean } | null>(null)
  const [loading, setLoading] = useState(false)

  const [enableForm] = Form.useForm()
  const [pwForm] = Form.useForm()
  const [codeForm] = Form.useForm()

  const [totpSetupState, setTotpSetupState] = useState<TotpSetupState>('idle')
  const [totpSecret, setTotpSecret] = useState('')
  const [totpUri, setTotpUri] = useState('')

  const loadStatus = async () => {
    try {
      const s = await apiFetch('/auth/status')
      setStatus(s)
    } catch {}
  }

  useEffect(() => { loadStatus() }, [])

  const handleEnable = async (values: { password: string; confirm: string }) => {
    if (values.password !== values.confirm) {
      msg.error('两次输入的密码不一致')
      return
    }
    setLoading(true)
    try {
      const d = await apiFetch('/auth/setup', {
        method: 'POST',
        body: JSON.stringify({ password: values.password }),
      })
      localStorage.setItem('auth_token', d.access_token)
      msg.success('密码保护已启用')
      enableForm.resetFields()
      await loadStatus()
    } catch (e: any) {
      msg.error(e.message)
    } finally {
      setLoading(false)
    }
  }

  const handleDisableAuth = async () => {
    setLoading(true)
    try {
      await apiFetch('/auth/disable', { method: 'POST' })
      localStorage.removeItem('auth_token')
      msg.success('密码保护已关闭')
      await loadStatus()
    } catch (e: any) {
      msg.error(e.message)
    } finally {
      setLoading(false)
    }
  }

  const handleChangePassword = async (values: { current_password: string; new_password: string; confirm: string }) => {
    if (values.new_password !== values.confirm) {
      msg.error('两次输入的新密码不一致')
      return
    }
    setLoading(true)
    try {
      await apiFetch('/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({ current_password: values.current_password, new_password: values.new_password }),
      })
      msg.success('密码已更新')
      pwForm.resetFields()
    } catch (e: any) {
      msg.error(e.message)
    } finally {
      setLoading(false)
    }
  }

  const handleSetupTotp = async () => {
    setLoading(true)
    try {
      const d = await apiFetch('/auth/2fa/setup')
      setTotpSecret(d.secret)
      setTotpUri(d.uri)
      setTotpSetupState('setup')
    } catch (e: any) {
      msg.error(e.message)
    } finally {
      setLoading(false)
    }
  }

  const handleEnableTotp = async (values: { code: string }) => {
    setLoading(true)
    try {
      await apiFetch('/auth/2fa/enable', {
        method: 'POST',
        body: JSON.stringify({ secret: totpSecret, code: values.code }),
      })
      msg.success('双因素认证已启用')
      setTotpSetupState('idle')
      codeForm.resetFields()
      await loadStatus()
    } catch (e: any) {
      msg.error(e.message)
    } finally {
      setLoading(false)
    }
  }

  const handleDisableTotp = async () => {
    setLoading(true)
    try {
      await apiFetch('/auth/2fa/disable', { method: 'POST' })
      msg.success('双因素认证已关闭')
      await loadStatus()
    } catch (e: any) {
      msg.error(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Card
        title="访问密码保护"
        extra={
          status?.has_password
            ? <Tag color="green"><CheckCircleOutlined /> 已启用</Tag>
            : <Tag color="default"><CloseCircleOutlined /> 未启用</Tag>
        }
      >
        {!status?.has_password ? (
          <Space direction="vertical" style={{ width: '100%' }}>
            <Typography.Text type="secondary">
              启用后，访问页面需要输入密码。默认不开启，任何能访问此地址的人均可使用。
            </Typography.Text>
            <Form form={enableForm} layout="vertical" onFinish={handleEnable} requiredMark={false} style={{ maxWidth: 360, marginTop: 8 }}>
              <Form.Item name="password" label="设置访问密码" rules={[{ required: true, message: '请输入密码' }, { min: 6, message: '至少 6 位' }]}>
                <Input.Password placeholder="至少 6 位" />
              </Form.Item>
              <Form.Item name="confirm" label="确认密码" rules={[{ required: true, message: '请再次输入' }]}>
                <Input.Password placeholder="再次输入密码" />
              </Form.Item>
              <Form.Item style={{ marginBottom: 0 }}>
                <Button type="primary" htmlType="submit" loading={loading} icon={<LockOutlined />}>
                  启用密码保护
                </Button>
              </Form.Item>
            </Form>
          </Space>
        ) : (
          <Space direction="vertical" style={{ width: '100%' }}>
            <Typography.Text type="secondary">当前已启用密码保护，关闭后任何人无需密码即可访问。</Typography.Text>
            <Button danger loading={loading} onClick={handleDisableAuth}>
              关闭密码保护
            </Button>
          </Space>
        )}
      </Card>

      {status?.has_password && (
        <>
          <Card title="修改密码">
            <Form form={pwForm} layout="vertical" onFinish={handleChangePassword} requiredMark={false} style={{ maxWidth: 360 }}>
              <Form.Item name="current_password" label="当前密码" rules={[{ required: true, message: '请输入当前密码' }]}>
                <Input.Password placeholder="当前密码" />
              </Form.Item>
              <Form.Item name="new_password" label="新密码" rules={[{ required: true, message: '请输入新密码' }, { min: 6, message: '至少 6 位' }]}>
                <Input.Password placeholder="新密码（至少 6 位）" />
              </Form.Item>
              <Form.Item name="confirm" label="确认新密码" rules={[{ required: true, message: '请再次输入' }]}>
                <Input.Password placeholder="再次输入新密码" />
              </Form.Item>
              <Form.Item style={{ marginBottom: 0 }}>
                <Button type="primary" htmlType="submit" loading={loading} icon={<SaveOutlined />}>
                  更新密码
                </Button>
              </Form.Item>
            </Form>
          </Card>

          <Card
            title="双因素认证 (2FA)"
            extra={
              status?.has_totp
                ? <Tag color="green"><CheckCircleOutlined /> 已启用</Tag>
                : <Tag color="default"><CloseCircleOutlined /> 未启用</Tag>
            }
          >
            {status?.has_totp ? (
              <Space direction="vertical">
                <Typography.Text type="secondary">
                  登录时需输入 Google Authenticator / Authy 等 App 中的 6 位验证码。
                </Typography.Text>
                <Button danger loading={loading} onClick={handleDisableTotp}>
                  关闭双因素认证
                </Button>
              </Space>
            ) : totpSetupState === 'idle' ? (
              <Space direction="vertical">
                <Typography.Text type="secondary">
                  启用后，登录时除密码外还需输入验证器 App 中的 6 位验证码，大幅提升安全性。
                </Typography.Text>
                <Button type="primary" loading={loading} onClick={handleSetupTotp} icon={<SafetyOutlined />}>
                  开启双因素认证
                </Button>
              </Space>
            ) : (
              <Space direction="vertical" style={{ width: '100%' }}>
                <Typography.Text strong>1. 用验证器 App 扫描下方二维码</Typography.Text>
                <div style={{ display: 'flex', gap: 24, alignItems: 'flex-start', flexWrap: 'wrap' }}>
                  <QRCode value={totpUri} size={180} />
                  <div style={{ flex: 1 }}>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>无法扫码？手动输入密钥：</Typography.Text>
                    <Typography.Paragraph copyable style={{ fontFamily: 'monospace', fontSize: 13, marginTop: 4 }}>
                      {totpSecret}
                    </Typography.Paragraph>
                  </div>
                </div>
                <Typography.Text strong>2. 输入 App 中显示的 6 位验证码以确认绑定</Typography.Text>
                <Form form={codeForm} layout="inline" onFinish={handleEnableTotp}>
                  <Form.Item name="code" rules={[{ required: true, message: '请输入验证码' }, { len: 6, message: '6 位数字' }]}>
                    <Input placeholder="000000" maxLength={6} style={{ width: 140, letterSpacing: 4, textAlign: 'center' }} />
                  </Form.Item>
                  <Form.Item>
                    <Button type="primary" htmlType="submit" loading={loading}>确认启用</Button>
                  </Form.Item>
                  <Form.Item>
                    <Button onClick={() => setTotpSetupState('idle')}>取消</Button>
                  </Form.Item>
                </Form>
              </Space>
            )}
          </Card>
        </>
      )}
    </div>
  )
}

export default function Settings() {
  const [form] = Form.useForm()
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [activeTab, setActiveTab] = useState('register')

  useEffect(() => {
    apiFetch('/config').then((data) => {
      if (!data.mail_provider) {
        data.mail_provider = 'luckmail'
      }
      if (!data.chatgpt_phone_source) {
        data.chatgpt_phone_source =
          ['smsbower', '5sim', 'herosms'].includes(String(data.sms_provider || ''))
            ? String(data.sms_provider)
            : 'smsbower'
      }
      if (!data.gptmail_base_url) {
        data.gptmail_base_url = 'https://mail.chatgpt.org.uk'
      }
      if (!data.maliapi_base_url) {
        data.maliapi_base_url = 'https://maliapi.215.im/v1'
      }
      if (!data.luckmail_base_url) {
        data.luckmail_base_url = 'https://mails.luckyous.com/'
      }
      data.cfworker_domains = parseStoredDomainList(data.cfworker_domains)
      data.cfworker_enabled_domains = parseStoredDomainList(data.cfworker_enabled_domains)
      data.cfworker_random_subdomain = parseBooleanConfigValue(data.cfworker_random_subdomain)
      form.setFieldsValue(data)
    })
  }, [form])

  const save = async () => {
    setSaving(true)
    try {
      const values = form.getFieldsValue(true)
      const domains = normalizeDomainList(values.cfworker_domains)
      const enabledDomains = normalizeDomainList(values.cfworker_enabled_domains).filter((domain) => domains.includes(domain))

      if (domains.length > 0 && enabledDomains.length === 0) {
        setActiveTab('mailbox')
        message.error('CF Worker 至少需要启用一个域名')
        return
      }

      values.cfworker_domains = JSON.stringify(domains)
      values.cfworker_enabled_domains = JSON.stringify(enabledDomains)
      if (domains.length > 0) {
        values.cfworker_domain = ''
      }
      values.cfworker_random_subdomain = parseBooleanConfigValue(values.cfworker_random_subdomain)

      await apiFetch('/config', { method: 'PUT', body: JSON.stringify({ data: values }) })
      form.setFieldsValue({
        cfworker_domains: domains,
        cfworker_enabled_domains: enabledDomains,
        cfworker_domain: domains.length > 0 ? '' : values.cfworker_domain,
        cfworker_random_subdomain: values.cfworker_random_subdomain,
      })
      message.success('保存成功')
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } finally {
      setSaving(false)
    }
  }

  const currentTab = TAB_ITEMS.find((t) => t.key === activeTab) as TabConfig
  const selectedMailProvider = Form.useWatch('mail_provider', form) || 'luckmail'
  const currentSections = useMemo(() => {
    if (activeTab === 'mailbox') {
      const providerSections = currentTab.sections.filter((section) => section.provider)
      const activeProviderSection =
        providerSections.find((section) => section.provider === selectedMailProvider) || providerSections[0]
      return [
        ...(activeProviderSection ? [activeProviderSection] : []),
        ...currentTab.sections.filter(
          (section) =>
            !section.provider &&
            !sectionHasField(section, 'mail_provider') &&
            !isSmsProviderSection(section) &&
            !isSmstomeSection(section),
        ),
        ...(selectedMailProvider === 'cfworker'
          ? [{ title: 'CF Worker 域名池', desc: '管理可用域名和启用状态', fields: [] }]
          : []),
      ]
    }
    if (activeTab === 'chatgpt') {
      return [
        { title: 'ChatGPT 手机验证', desc: '管理 add-phone 相关接码能力', fields: [] },
        ...currentTab.sections.filter(
          (section) => !isSmsProviderSection(section) && !isSmstomeSection(section) && !isLegacyPaymentSection(section),
        ),
      ]
    }
    return currentTab.sections
  }, [activeTab, currentTab.sections, selectedMailProvider])

  return (
    <div className="page-container">
      <PageHeader
        eyebrow="Config"
        title="配置中心"
        subtitle="把常用配置集中到更短的操作路径里，减少滚动和来回切换。"
      />

      <div className="settings-layout">
        <div className="settings-layout__nav">
          <Tabs
            tabPosition="left"
            activeKey={activeTab}
            onChange={setActiveTab}
            items={TAB_ITEMS.map((t) => ({
              key: t.key,
              label: (
                <span>
                  {t.icon}
                  <span style={{ marginLeft: 8 }}>{TAB_LABEL_OVERRIDES[t.key] || t.label}</span>
                </span>
              ),
            }))}
          />
        </div>

        <div className="settings-layout__content">
          {currentSections.length > 1 ? (
            <Card bordered={false} className="settings-section-nav" style={{ marginBottom: 16 }}>
              <Space wrap>
                {currentSections.map((section) => (
                  <Button
                    key={section.title}
                    size="small"
                    onClick={() => {
                      const node = document.getElementById(`settings-section-${section.title}`)
                      node?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                    }}
                  >
                    {getSectionDisplayTitle(section)}
                  </Button>
                ))}
              </Space>
            </Card>
          ) : null}
          {activeTab === 'integrations' ? (
            <IntegrationsPanel />
          ) : activeTab === 'security' ? (
            <SecurityPanel />
          ) : activeTab === 'claude' ? (
            <ClaudeImportPanel />
          ) : (
            <Form form={form} layout="vertical">
              {activeTab === 'captcha' ? <SolverStatusCompact /> : null}
              {activeTab === 'chatgpt' ? (
                <>
                  <ChatGPTFlowSummary form={form} />
                  <ChatGPTManualPlusSection />
                </>
              ) : null}
              {activeTab === 'mailbox' ? (
                <>
                  <UnifiedMailboxSections form={form} sections={currentTab.sections} />
                  {selectedMailProvider === 'cfworker' ? <CFWorkerDomainPoolSection form={form} /> : null}
                </>
              ) : activeTab === 'chatgpt' ? (
                <ChatGPTPhoneSectionsStacked form={form} sections={currentTab.sections} />
              ) : (
                currentTab.sections.map((section) => <ConfigSection key={section.title} form={form} section={section} />)
              )}
            </Form>
          )}
        </div>
      </div>
      {activeTab !== 'integrations' && activeTab !== 'security' && activeTab !== 'claude' ? (
        <div className="settings-save-bar">
          <Typography.Text type="secondary">
            {saved ? '配置已保存' : '修改后记得保存，当前页的调整会立即保留到配置文件。'}
          </Typography.Text>
          <Button type="primary" icon={<SaveOutlined />} onClick={save} loading={saving}>
            {saved ? '已保存' : '保存设置'}
          </Button>
        </div>
      ) : null}
    </div>
  )
}
