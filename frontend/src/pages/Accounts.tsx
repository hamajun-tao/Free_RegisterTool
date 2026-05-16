import { useEffect, useState, useCallback, useMemo, useRef } from 'react'
import { useParams } from 'react-router-dom'
import {
  Table,
  Button,
  Input,
  InputNumber,
  Select,
  Tag,
  Space,
  Modal,
  Form,
  Checkbox,
  message,
  Popconfirm,
  Dropdown,
  Typography,
  Alert,
  theme,
  Card,
  Drawer,
  Grid,
  Pagination,
} from 'antd'
import type { MenuProps } from 'antd'
import {
  ReloadOutlined,
  CopyOutlined,
  LinkOutlined,
  PlusOutlined,
  DownOutlined,
  DownloadOutlined,
  UploadOutlined,
  MoreOutlined,
  DeleteOutlined,
  SyncOutlined,
  UpOutlined,
  LoadingOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  ExclamationCircleOutlined,
} from '@ant-design/icons'
import { ChatGPTRegistrationModeSwitch } from '@/components/ChatGPTRegistrationModeSwitch'
import { TaskLogPanel } from '@/components/TaskLogPanel'
import { usePersistentChatGPTRegistrationMode } from '@/hooks/usePersistentChatGPTRegistrationMode'
import { buildChatGPTRegistrationRequestAdapter } from '@/lib/chatgptRegistrationRequestAdapter'
import { apiFetch } from '@/lib/utils'
import { normalizeExecutorForPlatform } from '@/lib/platformExecutorOptions'
import type { CheckboxChangeEvent } from 'antd/es/checkbox'
import { useRegisterTask } from '@/contexts/RegisterTaskContext'
import { buildRegisterExtra } from '@/lib/registerConfigMapper'

import { MIX_PROVIDER_OPTIONS, DEFAULT_PARALLEL_MAIL_MIX, resolveConfiguredMixOptions } from '@/lib/mailProviders'

const { Text } = Typography
const { useBreakpoint } = Grid

type AccountsViewMode = 'table-dense' | 'table-compact' | 'card-list'

function normalizeTaskMeta(task: any) {
  const progress = String(task?.progress || '0/0')
  const parts = progress.split('/')
  const parsedCompleted = parseInt(parts[0] || '0', 10)
  const parsedTotal = parseInt(parts[1] || '0', 10)
  const completed = Number(task?.completed ?? parsedCompleted) || 0
  const total = Number(task?.total ?? parsedTotal) || 0
  const success = Number(task?.success ?? 0) || 0
  const skipped = Number(task?.skipped ?? 0) || 0
  const started = Number(task?.started ?? 0) || 0
  return {
    progress: `${completed}/${total}`,
    total,
    started,
    completed,
    success,
    skipped,
    errors: Array.isArray(task?.errors) ? task.errors : [],
    status: task?.status,
  }
}

const STATUS_COLORS: Record<string, string> = {
  registered: 'default',
  trial: 'success',
  subscribed: 'success',
  expired: 'warning',
  invalid: 'error',
}

const INVALID_REASON_LABELS: Record<string, string> = {
  db_status: '数据库标记',
  auth_401: '认证失效(401)',
  auth_deactivated: '账号已停用',
  auth_403: '账号被封(403)',
  codex_401: 'Codex认证失效',
  codex_deactivated: 'Codex已停用',
  codex_403: 'Codex被封',
  remote_401: '远端认证失效',
  remote_deactivated: '远端已停用',
  remote_403: '远端被封',
  remote_unreachable: '远端不可达',
  remote_server_error: '远端服务器错误',
  local_connection_error: '本地探测超时',
}

function parseExtraJson(raw: string | undefined) {
  if (!raw) return {}
  try {
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

function normalizeAccount(account: any) {
  const email = String(account.email || '').trim()
  const password = String(account.password || '').trim()
  
  if (!account.extra_json) {
    return {
      ...account,
      email,
      password,
      extra: {},
      sub2apiSync: account.sub2api_sync || {},
      chatgptLocal: account.chatgpt_local || {},
      effectiveStatus: account.effective_status || account.status || 'registered',
      invalidReason: account.invalid_reason || '',
    }
  }
  const extra = parseExtraJson(account.extra_json)
  const syncStatuses = extra.sync_statuses && typeof extra.sync_statuses === 'object' ? extra.sync_statuses : {}
  const sub2apiSync = syncStatuses.sub2api && typeof syncStatuses.sub2api === 'object' ? syncStatuses.sub2api : {}
  const chatgptLocal = extra.chatgpt_local && typeof extra.chatgpt_local === 'object' ? extra.chatgpt_local : {}
  const effectiveStatus = account.effective_status || account.status || 'registered'
  const invalidReason = account.invalid_reason || ''
  return { ...account, email, password, extra, sub2apiSync, chatgptLocal, effectiveStatus, invalidReason }
}

function extractRefreshToken(record: any): string {
  try {
    if (record?.extra && typeof record.extra === 'object') {
      return String(record.extra.refresh_token || '').trim()
    }
    const extra = JSON.parse(record?.extra_json || '{}')
    return String(extra.refresh_token || '').trim()
  } catch {
    return ''
  }
}

function parseApiDate(value?: string) {
  if (!value) return null
  const normalized = /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : `${value}Z`
  const date = new Date(normalized)
  return Number.isNaN(date.getTime()) ? null : date
}

function formatSyncTime(value?: string) {
  if (!value) return ''
  const date = parseApiDate(value)
  if (!date) return value
  return date.toLocaleString()
}

function formatCreatedAt(value?: string) {
  if (!value) return { date: '-', time: '' }
  const date = parseApiDate(value)
  if (!date) {
    return { date: value, time: '' }
  }
  return {
    date: date.toLocaleDateString(),
    time: date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
  }
}

function authStateMeta(state?: string) {
  switch (state) {
    case 'access_token_valid':
      return { color: 'success', label: 'AT有效' }
    case 'account_deactivated':
      return { color: 'error', label: '已失效' }
    case 'access_token_invalidated':
      return { color: 'error', label: 'AT失效' }
    case 'unauthorized':
      return { color: 'error', label: '未授权' }
    case 'missing_access_token':
      return { color: 'default', label: '缺少AT' }
    case 'banned_like':
      return { color: 'error', label: '疑似封禁' }
    case 'probe_failed':
      return { color: 'warning', label: '探测失败' }
    default:
      return { color: 'default', label: '未探测' }
  }
}

function codexStateMeta(state?: string) {
  switch (state) {
    case 'usable':
      return { color: 'success', label: '可用' }
    case 'account_deactivated':
      return { color: 'error', label: '已失效' }
    case 'access_token_invalidated':
      return { color: 'error', label: 'AT失效' }
    case 'unauthorized':
      return { color: 'error', label: '未授权' }
    case 'payment_required':
      return { color: 'warning', label: '需付费/权限' }
    case 'quota_exhausted':
      return { color: 'warning', label: '额度耗尽' }
    case 'skipped_auth_invalid':
      return { color: 'default', label: '未测' }
    case 'probe_failed':
      return { color: 'warning', label: '探测失败' }
    default:
      return { color: 'default', label: '未探测' }
  }
}

function planMeta(plan?: string) {
  switch ((plan || '').toLowerCase()) {
    case 'plus':
      return { color: 'success', label: 'Plus' }
    case 'team':
      return { color: 'processing', label: 'Team' }
    case 'enterprise':
      return { color: 'processing', label: 'Enterprise' }
    case 'pro':
      return { color: 'processing', label: 'Pro' }
    case 'free':
      return { color: 'default', label: 'Free' }
    default:
      return { color: 'default', label: '未知' }
  }
}

const PROVIDER_LABELS: Record<string, string> = {
  paypal_web: 'PayPal Web',
  gopay_api: 'GoPay API',
  gopay_android: 'GoPay Android',
  manual_link: '手动支付',
  card: '信用卡',
  paypal: 'PayPal',
  gopay: 'GoPay',
}

const DIAG_LABELS: Record<string, string> = {
  // PayPal Web
  datadome_slider: 'DataDome滑块',
  datadome_ip_blocked: 'IP被封',
  datadome_slider_failed: 'DataDome失败',
  hcaptcha_timeout: 'hCaptcha超时',
  hcaptcha_failed: 'hCaptcha失败',
  hcaptcha_paypal_failed: 'PayPal验证码',
  captcha_key_missing: '缺打码Key',
  hermes_params_missing: 'Hermes参数缺失',
  hermes_http_failed: 'Hermes失败',
  paypal_callback_timeout: '回调超时',
  paypal_browser_auth: '浏览器授权失败',
  paypal_consent_missing: '缺consent按钮',
  // Checkout/Card
  checkout_auth_error: 'Checkout认证错误',
  checkout_400: 'Checkout 400',
  checkout_404: 'Checkout 404',
  card_declined: '卡被拒',
  card_insufficient: '余额不足',
  // GoPay API
  gopay_otp_timeout: 'OTP超时',
  gopay_pin_failed: 'PIN失败',
  gopay_linking_failed: '链接失败',
  gopay_midtrans_failed: 'Midtrans失败',
  terminal_failure: '终态失败',
  // GoPay Android
  no_adb: '缺少ADB',
  no_avd: '无AVD镜像',
  emulator_boot_timeout: '模拟器超时',
  play_services_missing: '缺Play Services',
  network_down: '网络不通',
  app_not_installed: 'App未安装',
  app_install_failed: '安装失败',
  app_launch_failed: '启动失败',
  play_integrity_blocked: 'Play Integrity拦截',
  no_phone_number: '缺手机号',
  no_otp_provider: '缺OTP配置',
  no_gopay_pin: '缺GoPay PIN',
  login_ui_not_found: '登录页未找到',
  otp_input_timeout: 'OTP超时',
  otp_sms_read_failed: 'SMS读取失败',
  otp_verify_failed: 'OTP验证失败',
  auth_page_not_reached: '授权页未达',
  pin_entry_failed: 'PIN输入失败',
  pin_verify_failed: 'PIN验证失败',
  payment_confirm_timeout: '支付确认超时',
  skipped_not_free: 'Promo未生效/已跳过',
  android_exception: '模拟器异常',
  auth_ready_no_payment: '授权可达/未付款',
}

function paymentStateMeta(state?: string) {
  switch ((state || '').toLowerCase()) {
    case 'succeeded': return { color: 'success', label: '已支付' }
    case 'subscribed': return { color: 'success', label: '已订阅' }
    case 'skipped_not_free': return { color: 'warning', label: '跳过(非Free)' }
    case 'manual_link_pending': return { color: 'processing', label: '待手动支付' }
    case 'declined': return { color: 'error', label: '被拒' }
    case 'captcha_failed': return { color: 'error', label: '验证码失败' }
    case 'no_result': return { color: 'error', label: '无结果' }
    case 'payment_confirm_timeout': return { color: 'warning', label: '支付确认超时' }
    case 'experiment_auth_ready': return { color: 'warning', label: '实验:授权可达' }
    case 'experiment_incomplete': return { color: 'warning', label: '实验:未完成' }
    case 'experiment_error': return { color: 'error', label: '实验:异常' }
    default:
      if (state && state.startsWith('failed')) return { color: 'error', label: '失败' }
      return state ? { color: 'default', label: state } : { color: 'default', label: '' }
  }
}

function PaymentLinkCell({ url }: { url?: string }) {
  if (!url) return <span style={{ color: '#ccc' }}>-</span>
  return (
    <Space size={0}>
      <Button type="text" size="small" icon={<CopyOutlined />} onClick={() => navigator.clipboard.writeText(url)} />
      <Button type="text" size="small" icon={<LinkOutlined />} onClick={() => window.open(url, '_blank', 'noopener,noreferrer')} />
    </Space>
  )
}

function statusTagMeta(status: string) {
  return { color: STATUS_COLORS[status] || 'default', label: status || 'registered' }
}

function useElementWidth<T extends HTMLElement>() {
  const ref = useRef<T | null>(null)
  const [width, setWidth] = useState(0)

  useEffect(() => {
    const node = ref.current
    if (!node || typeof ResizeObserver === 'undefined') return

    const observer = new ResizeObserver((entries) => {
      const nextWidth = entries[0]?.contentRect?.width || 0
      setWidth(nextWidth)
    })

    observer.observe(node)
    setWidth(node.getBoundingClientRect().width)
    return () => observer.disconnect()
  }, [])

  return [ref, width] as const
}

function formatStructuredText(value?: string) {
  if (!value) return ''
  const trimmed = String(value).trim()
  if (!trimmed) return ''
  if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
    try {
      return JSON.stringify(JSON.parse(trimmed), null, 2)
    } catch {
      return trimmed
    }
  }
  return trimmed
}

function SummaryField({
  label,
  value,
  code = false,
}: {
  label: string
  value?: string
  code?: boolean
}) {
  const { token } = theme.useToken()
  if (!value) return null

  const content = code ? formatStructuredText(value) : value
  const isBlock = code || content.length > 96 || content.includes('\n')

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '104px minmax(0, 1fr)',
        gap: 12,
        alignItems: 'start',
      }}
    >
      <Text type="secondary" style={{ fontSize: 12, lineHeight: '20px' }}>
        {label}
      </Text>
      {isBlock ? (
        <pre
          style={{
            margin: 0,
            padding: code ? '8px 10px' : 0,
            borderRadius: code ? token.borderRadius : 0,
            border: code ? `1px solid ${token.colorBorder}` : 'none',
            background: code ? token.colorBgElevated : 'transparent',
            color: code ? token.colorText : token.colorTextSecondary,
            fontFamily: code ? 'SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace' : 'inherit',
            fontSize: 12,
            lineHeight: 1.6,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            overflowWrap: 'anywhere',
            maxHeight: code ? 160 : 'none',
            overflow: code ? 'auto' : 'visible',
          }}
        >
          {content}
        </pre>
      ) : (
        <Text style={{ display: 'block', color: token.colorTextSecondary, lineHeight: '20px' }}>
          {content}
        </Text>
      )}
    </div>
  )
}

function DetailSection({ title, children }: { title: string; children: React.ReactNode }) {
  const { token } = theme.useToken()

  return (
    <div
      style={{
        marginTop: 16,
        padding: 14,
        borderRadius: token.borderRadiusLG,
        border: `1px solid ${token.colorBorder}`,
        background: token.colorFillAlter,
      }}
    >
      <div style={{ marginBottom: 10, fontWeight: 600, color: token.colorText }}>{title}</div>
      {children}
    </div>
  )
}

function LocalProbeSummary({ probe }: { probe: any }) {
  const checkedAt = probe?.checked_at || probe?.auth?.checked_at || probe?.subscription?.checked_at || probe?.codex?.checked_at
  const auth = probe?.auth || {}
  const subscription = probe?.subscription || {}
  const codex = probe?.codex || {}

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        <Tag color={authStateMeta(auth.state).color}>认证: {authStateMeta(auth.state).label}</Tag>
        <Tag color={planMeta(subscription.plan).color}>订阅: {planMeta(subscription.plan).label}</Tag>
        <Tag color={codexStateMeta(codex.state).color}>Codex: {codexStateMeta(codex.state).label}</Tag>
      </div>
      <SummaryField label="探测时间" value={checkedAt ? formatSyncTime(checkedAt) : ''} />
      <SummaryField label="认证信息" value={auth.message} code />
      <SummaryField label="工作区套餐" value={subscription.workspace_plan_type} />
      <SummaryField label="Codex 信息" value={codex.message} code />
    </div>
  )
}

function sub2ApiStateMeta(sync: any) {
  if (!sync || Object.keys(sync).length === 0) {
    return { color: 'default', label: '未检查' }
  }
  if (sync.remote_state === 'unconfigured') {
    return { color: 'warning', label: '未配置' }
  }
  if (sync.remote_state === 'unreachable') {
    return { color: 'error', label: '无法连接' }
  }
  if (sync.remote_state === 'not_found') {
    return { color: 'default', label: '远端不存在' }
  }
  if (sync.remote_state === 'exists') {
    return { color: 'success', label: '远端已存在' }
  }
  if (sync.remote_state === 'created_unverified') {
    return { color: 'processing', label: '已上传待确认' }
  }
  return sync.ok ? { color: 'processing', label: '已同步' } : { color: 'warning', label: '待处理' }
}

function Sub2ApiSyncSummary({ sync }: { sync: any }) {
  const meta = sub2ApiStateMeta(sync)
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        <Tag color={meta.color}>{meta.label}</Tag>
        {sync?.remote_account_id ? <Tag>{`ID: ${sync.remote_account_id}`}</Tag> : null}
      </div>
      <SummaryField label="远端状态" value={sync?.remote_state} />
      <SummaryField label="同步信息" value={sync?.message || sync?.last_message} code />
      <SummaryField label="检查时间" value={sync?.last_attempt_at ? formatSyncTime(sync.last_attempt_at) : ''} />
      <SummaryField label="上传时间" value={sync?.uploaded_at ? formatSyncTime(sync.uploaded_at) : ''} />
    </div>
  )
}

function CompactStatusTags({
  authState,
  plan,
  codexState,
}: {
  authState?: string
  plan?: string
  codexState?: string
}) {
  const authMeta = authStateMeta(authState)
  const planTag = planMeta(plan)
  const codexMeta = codexStateMeta(codexState)

  return (
    <div className="compact-pill-list">
      <Tag color={authMeta.color}>{authMeta.label}</Tag>
      <Tag color={planTag.color}>{planTag.label}</Tag>
      <Tag color={codexMeta.color}>Codex {codexMeta.label}</Tag>
    </div>
  )
}

function AccountCard({
  account,
  selected,
  onToggleSelect,
  onOpenDetail,
  onDelete,
  actions,
  onRefresh,
}: {
  account: any
  selected: boolean
  onToggleSelect: (checked: boolean) => void
  onOpenDetail: () => void
  onDelete: () => void
  actions: any[]
  onRefresh: () => void | Promise<void>
}) {
  const status = account.effectiveStatus || account.status || 'registered'
  const statusMeta = statusTagMeta(status)
  const sub2apiMeta = sub2ApiStateMeta(account.sub2apiSync || {})
  const rt = extractRefreshToken(account)

  return (
    <div className={`account-card${selected ? ' account-card--selected' : ''}`}>
      <div className="account-card__head">
        <Checkbox checked={selected} onChange={(e) => onToggleSelect(e.target.checked)} />
        <div className="account-card__identity" onClick={onOpenDetail}>
          <Text className="mono-text" ellipsis={{ tooltip: account.email }}>
            {account.email}
          </Text>
          <Text type="secondary" ellipsis={{ tooltip: account.user_id || `账号 #${account.id}` }}>
            {account.user_id ? `UID: ${account.user_id}` : `账号 #${account.id}`}
          </Text>
        </div>
        <ActionMenu acc={account} onRefresh={onRefresh} actions={actions} onDelete={onDelete} />
      </div>

      <div className="account-card__body">
        <div className="account-card__row">
          <span className="account-card__label">状态</span>
          <Tag color={statusMeta.color}>{statusMeta.label}</Tag>
        </div>
        {account.platform === 'chatgpt' ? (
          <>
            <div className="account-card__row account-card__row--stack">
              <span className="account-card__label">本地状态</span>
              <CompactStatusTags
                authState={account.chatgptLocal?.auth?.state}
                plan={account.chatgptLocal?.subscription?.plan}
                codexState={account.chatgptLocal?.codex?.state}
              />
            </div>
            <div className="account-card__row">
              <span className="account-card__label">Sub2API</span>
              <Tag color={sub2apiMeta.color}>{sub2apiMeta.label}</Tag>
            </div>
          </>
        ) : null}
        <div className="account-card__row">
          <span className="account-card__label">RT</span>
          {rt ? <Text className="mono-text">{`${rt.slice(0, 18)}...`}</Text> : <Text type="secondary">无</Text>}
        </div>
      </div>

      <div className="account-card__foot">
        <Button type="link" size="small" onClick={onOpenDetail}>
          详情
        </Button>
        <PaymentLinkCell url={account.cashier_url} />
      </div>
    </div>
  )
}

function ActionMenu({
  acc,
  onRefresh,
  actions,
  onDelete,
}: {
  acc: any
  onRefresh: () => void | Promise<void>
  actions: any[]
  onDelete?: () => void | Promise<void>
}) {
  const [resultOpen, setResultOpen] = useState(false)
  const [resultTitle, setResultTitle] = useState('')
  const [resultStatus, setResultStatus] = useState<'success' | 'error'>('success')
  const [resultText, setResultText] = useState('')
  const [resultUrl, setResultUrl] = useState('')
  const [resultProbe, setResultProbe] = useState<any>(null)
  const [resultRemoteSync, setResultRemoteSync] = useState<any>(null)

  const showResult = (title: string, status: 'success' | 'error', text: string, url = '', probe: any = null, remoteSync: any = null) => {
    setResultTitle(title)
    setResultStatus(status)
    setResultText(text)
    setResultUrl(url)
    setResultProbe(probe)
    setResultRemoteSync(remoteSync)
    setResultOpen(true)
  }

  const copyResultUrl = async () => {
    if (!resultUrl) return
    try {
      await navigator.clipboard.writeText(resultUrl)
      message.success('链接已复制')
    } catch {
      message.error('复制失败')
    }
  }

  const handleAction = async (actionId: string) => {
    const actionLabel = actions.find((item) => item.id === actionId)?.label || actionId

    try {
      const r = await apiFetch(`/actions/${acc.platform}/${acc.id}/${actionId}`, {
        method: 'POST',
        body: JSON.stringify({ params: {} }),
      })
      if (!r.ok) {
        const data = r.data || {}
        const probe = typeof data === 'object' && data ? data.probe || null : null
        const remoteSync = typeof data === 'object' && data ? data.sync || null : null
        showResult(actionLabel, 'error', r.error || data.message || '操作失败', '', probe, remoteSync)
        try {
          await onRefresh()
        } catch {
          message.warning('Result received, but list refresh failed. Please refresh manually.')
        }
        return
      }
      const data = r.data || {}
      if (data.url || data.checkout_url || data.cashier_url) {
        const targetUrl = data.url || data.checkout_url || data.cashier_url
        message.success('链接已生成')
        showResult(actionLabel, 'success', '操作成功，请在弹窗中打开或复制链接。', targetUrl)
      } else {
        message.success(data.message || '操作成功')
        const probe = typeof data === 'object' && data ? data.probe || null : null
        const remoteSync = typeof data === 'object' && data ? data.sync || null : null
        const text =
          probe
            ? String(data.message || '操作成功')
            : remoteSync
            ? String(data.message || '操作成功')
            : typeof data === 'string'
            ? data
            : Object.keys(data).length > 0
              ? JSON.stringify(data, null, 2)
              : '操作成功'
        showResult(actionLabel, 'success', text, '', probe, remoteSync)
      }
      try {
        await onRefresh()
      } catch {
        message.warning('Action succeeded, but list refresh failed. Please refresh manually.')
      }
    } catch (e: any) {
      const detail = e?.message ? String(e.message) : '请求失败'
      message.error(detail)
      showResult(actionLabel, 'error', detail)
    }
  }

  const menuItems: MenuProps['items'] = [
    ...actions.map((a) => ({
      key: `action:${a.id}`,
      label: a.label,
    })),
    ...(onDelete
      ? [
          ...(actions.length > 0 ? [{ type: 'divider' as const }] : []),
          { key: 'delete', label: '删除账号', danger: true },
        ]
      : []),
  ]

  if (menuItems.length === 0) return null

  return (
    <>
      <Dropdown
        trigger={['click']}
        menu={{
          items: menuItems,
          onClick: ({ key }) => {
            const menuKey = String(key)
            if (menuKey === 'delete') {
              Modal.confirm({
                title: '确认删除账号？',
                content: acc?.email || `账号 #${acc?.id}`,
                okText: '删除',
                okButtonProps: { danger: true },
                cancelText: '取消',
                onOk: async () => {
                  await onDelete?.()
                },
              })
              return
            }
            if (menuKey.startsWith('action:')) {
              void handleAction(menuKey.slice('action:'.length))
            }
          },
        }}
      >
        <Button className="account-action-more" size="small" icon={<MoreOutlined />}>
          更多
        </Button>
      </Dropdown>
      <Modal
        title={resultTitle}
        open={resultOpen}
        onCancel={() => setResultOpen(false)}
        footer={[
          resultUrl ? (
            <Button key="copy" onClick={copyResultUrl}>
              复制链接
            </Button>
          ) : null,
          resultUrl ? (
            <Button
              key="open"
              type="primary"
              onClick={() => window.open(resultUrl, '_blank', 'noopener,noreferrer')}
            >
              打开链接
            </Button>
          ) : null,
          <Button key="ok" type={resultUrl ? 'default' : 'primary'} onClick={() => setResultOpen(false)}>
            确定
          </Button>,
        ].filter(Boolean)}
        maskClosable={false}
      >
        <Alert
          type={resultStatus}
          showIcon
          message={resultStatus === 'success' ? '操作完成' : '操作失败'}
          style={{ marginBottom: 12 }}
        />
        {resultProbe ? (
          <div style={{ marginBottom: 12 }}>
            <LocalProbeSummary probe={resultProbe} />
          </div>
        ) : null}
        {resultRemoteSync ? (
          <div style={{ marginBottom: 12 }}>
            <Sub2ApiSyncSummary sync={resultRemoteSync} />
          </div>
        ) : null}
        {resultUrl ? (
          <Space direction="vertical" style={{ width: '100%' }}>
            <Text copyable={{ text: resultUrl }} style={{ wordBreak: 'break-all' }}>
              {resultUrl}
            </Text>
          </Space>
        ) : null}
        {resultText ? (
          <pre
            style={{
              margin: 0,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              fontFamily: 'monospace',
              fontSize: 12,
            }}
          >
            {resultText}
          </pre>
        ) : null}
      </Modal>
    </>
  )
}

function filterPlatformActionsByConfig(platform: string, actions: any[], cfg: Record<string, any>) {
  if (platform !== 'chatgpt') return actions

  const hasSub2Api = Boolean(String(cfg.sub2api_api_url || '').trim() && String(cfg.sub2api_api_key || '').trim())
  const hasTeamManager = Boolean(String(cfg.team_manager_url || '').trim() && String(cfg.team_manager_key || '').trim())
  const hasCodexProxy = Boolean(String(cfg.codex_proxy_url || '').trim() && String(cfg.codex_proxy_key || '').trim())

  return actions.filter((action: any) => {
    const actionId = String(action?.id || '')

    if (actionId === 'sync_cliproxyapi_status' || actionId === 'upload_cpa') return false
    if (actionId === 'sync_sub2api_status' || actionId === 'upload_sub2api') return hasSub2Api
    if (actionId === 'upload_tm') return hasTeamManager
    if (actionId === 'upload_codex_proxy') return hasCodexProxy
    if (actionId === 'payment_link') return true

    return true
  })
}

export default function Accounts() {
  const { platform } = useParams<{ platform: string }>()
  const { token } = theme.useToken()
  const screens = useBreakpoint()
  const [tableContainerRef, tableContainerWidth] = useElementWidth<HTMLDivElement>()
  const [currentPlatform, setCurrentPlatform] = useState(platform || 'trae')
  const [accounts, setAccounts] = useState<any[]>([])
  const [platformActions, setPlatformActions] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')
  const [filterStatus, setFilterStatus] = useState('')
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([])
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState<number>(() => {
    try {
      const saved = Number(localStorage.getItem('accounts_page_size'))
      if ([20, 50, 100].includes(saved)) return saved
    } catch {
      /* ignored */
    }
    return 20
  })

  const [registerModalOpen, setRegisterModalOpen] = useState(false)
  const [registerModalCollapsed, setRegisterModalCollapsed] = useState(false)
  const [addModalOpen, setAddModalOpen] = useState(false)
  const [importModalOpen, setImportModalOpen] = useState(false)
  const [detailModalOpen, setDetailModalOpen] = useState(false)
  const [currentAccount, setCurrentAccount] = useState<any>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  const [registerForm] = Form.useForm()
  const [addForm] = Form.useForm()
  const [detailForm] = Form.useForm()
  const { mode: chatgptRegistrationMode, setMode: setChatgptRegistrationMode } =
    usePersistentChatGPTRegistrationMode()
  const [importText, setImportText] = useState('')
  const [importLoading, setImportLoading] = useState(false)
  const [taskId, setTaskId] = useState<string | null>(null)
  const [registerLoading, setRegisterLoading] = useState(false)
  const { task: globalTask, startTask: startGlobalTask, clearTask: clearGlobalTask } = useRegisterTask()
  const [taskMeta, setTaskMeta] = useState<{ progress?: string; total?: number; started?: number; completed?: number; success?: number; skipped?: number; errors?: string[]; status?: string; worker_states?: any[] } | null>(null)
  const [statusSyncLoading, setStatusSyncLoading] = useState<
    'probe_selected' | 'probe_page' | 'probe_all' | 'sub2api_selected' | 'sub2api_page' | 'sub2api_all' | ''
  >('')
  const [sub2ApiUploadLoading, setSub2ApiUploadLoading] = useState<'selected' | 'page' | 'all' | ''>('')
  const [mixProviderOptions, setMixProviderOptions] = useState(MIX_PROVIDER_OPTIONS)
  const registerMailProvider = Form.useWatch('mail_provider', registerForm) || 'luckmail'
  const registerMailConfigOverrideEnabled = Form.useWatch('mail_config_override_enabled', registerForm)
  const registerMailProviderMixEnabled = Form.useWatch('mail_provider_mix_enabled', registerForm)
  const registerMailProviderMix = Form.useWatch('mail_provider_mix', registerForm) || []
  const selectedRegisterMailProviders = registerMailProviderMixEnabled
    ? Array.isArray(registerMailProviderMix) && registerMailProviderMix.length > 0
      ? registerMailProviderMix
      : mixProviderOptions.map((item) => item.value)
    : [registerMailProvider]

  const viewMode: AccountsViewMode = useMemo(() => {
    if (!screens.md) return 'card-list'
    if (!screens.xl) return 'table-compact'
    if (tableContainerWidth > 0 && tableContainerWidth < 1180) return 'table-compact'
    return 'table-dense'
  }, [screens.md, screens.xl, tableContainerWidth])

  useEffect(() => {
    if (platform) setCurrentPlatform(platform)
  }, [platform])

  useEffect(() => {
    apiFetch('/config')
      .then((cfg) => {
        const options = resolveConfiguredMixOptions(cfg || {})
        setMixProviderOptions(options)
        registerForm.setFieldsValue({
          mail_provider: cfg.mail_provider || 'luckmail',
          duckduckgo_email: cfg.duckduckgo_email || '',
          duckduckgo_gmail_address: cfg.duckduckgo_gmail_address || '',
          duckduckgo_gmail_app_password: cfg.duckduckgo_gmail_app_password || '',
          duckduckgo_imap_host: cfg.duckduckgo_imap_host || 'imap.gmail.com',
          duckduckgo_imap_port: cfg.duckduckgo_imap_port || '993',
          duckduckgo_mailbox: cfg.duckduckgo_mailbox || 'INBOX',
          duckduckgo_all_mailbox: cfg.duckduckgo_all_mailbox || '[Gmail]/All Mail',
          duckduckgo_gmail_api_mode: cfg.duckduckgo_gmail_api_mode || 'imap',
          duckduckgo_gmail_api_credentials: cfg.duckduckgo_gmail_api_credentials || '',
          duckduckgo_gmail_api_token: cfg.duckduckgo_gmail_api_token || '',
          duckduckgo_api_token: cfg.duckduckgo_api_token || '',
          duckduckgo_alias_mode: cfg.duckduckgo_alias_mode || 'fixed',
          duckduckgo_private_addresses: cfg.duckduckgo_private_addresses || '',
          duckduckgo_alias_rotation: cfg.duckduckgo_alias_rotation || 'random',
        })
      })
      .catch(() => {
        setMixProviderOptions(MIX_PROVIDER_OPTIONS.filter((item) => DEFAULT_PARALLEL_MAIL_MIX.includes(item.value)))
      })
  }, [registerForm])

  useEffect(() => {
    if (!registerMailProviderMixEnabled) return
    const currentMix = registerForm.getFieldValue('mail_provider_mix')
    if (!Array.isArray(currentMix) || currentMix.length === 0) {
      registerForm.setFieldValue('mail_provider_mix', mixProviderOptions.map((item) => item.value))
    }
  }, [registerForm, registerMailProviderMixEnabled, mixProviderOptions])

  useEffect(() => {
    if (taskId || !globalTask) return
    const globalTaskId = String(globalTask.task_id || globalTask.id || '').trim()
    if (!globalTaskId) return
    setTaskId(globalTaskId)
    setTaskMeta(normalizeTaskMeta(globalTask))
    setRegisterModalOpen(true)
    setRegisterModalCollapsed(true)
  }, [globalTask, taskId])

  // 轮询任务状态（收起态 & 展开态都持续轮询以保持数据同步）
  useEffect(() => {
    if (!taskId) { setTaskMeta(null); return }
    let timer: ReturnType<typeof setInterval> | null = null
    const poll = async () => {
      try {
        const t = await apiFetch(`/tasks/${taskId}?include_logs=0`)
        setTaskMeta(normalizeTaskMeta(t))
        if (t.status === 'done' || t.status === 'failed' || t.status === 'stopped') {
          if (timer) clearInterval(timer)
          load()
          window.setTimeout(() => {
            closeRegisterTaskPanel()
          }, 1200)
        }
      } catch { /* ignore */ }
    }
    void poll()
    timer = setInterval(poll, 2000)
    return () => { if (timer) clearInterval(timer) }
  }, [taskId])

  useEffect(() => {
    if (!detailModalOpen || !currentAccount) return
    detailForm.setFieldsValue({
      status: currentAccount.status,
      token: currentAccount.token,
      cashier_url: currentAccount.cashier_url,
    })
  }, [detailModalOpen, currentAccount, detailForm])

  useEffect(() => {
    setPage(1)
    setSelectedRowKeys([])
  }, [currentPlatform, search, filterStatus])

  const closeRegisterTaskPanel = useCallback(() => {
    setRegisterModalOpen(false)
    setRegisterModalCollapsed(false)
    setTaskId(null)
    setTaskMeta(null)
    registerForm.resetFields()
    try {
      clearGlobalTask()
    } catch {
      /* ignored */
    }
  }, [clearGlobalTask, registerForm])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ platform: currentPlatform, page: String(page), page_size: String(pageSize), summary: '1' })
      if (search) params.set('email', search)
      if (filterStatus) {
        if (filterStatus === 'group:success' || filterStatus === 'group:failed') {
          params.set('status_group', filterStatus.replace('group:', ''))
        } else {
          params.set('status', filterStatus)
        }
      }
      const data = await apiFetch(`/accounts?${params}`)
      setAccounts((data.items || []).map(normalizeAccount))
      setTotal(data.total)
    } finally {
      setLoading(false)
    }
  }, [currentPlatform, search, filterStatus, page, pageSize])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    const status = taskMeta?.status
    if (!taskId || (status !== 'done' && status !== 'failed' && status !== 'stopped')) return
    load()
    const timer = window.setTimeout(() => {
      closeRegisterTaskPanel()
    }, 1200)
    return () => window.clearTimeout(timer)
  }, [closeRegisterTaskPanel, load, taskId, taskMeta?.status])

  useEffect(() => {
    Promise.all([apiFetch(`/actions/${currentPlatform}`), apiFetch('/config')])
      .then(([data, cfg]) => {
        const actions = Array.isArray(data.actions) ? data.actions : []
        setPlatformActions(filterPlatformActionsByConfig(currentPlatform, actions, cfg || {}))
      })
      .catch(() => setPlatformActions([]))
  }, [currentPlatform])

  const copyText = (text: string) => {
    navigator.clipboard.writeText(text)
    message.success('已复制')
  }

  const openAccountDetail = async (record: any) => {
    setCurrentAccount(record)
    setDetailModalOpen(true)
    setDetailLoading(true)
    try {
      const full = await apiFetch(`/accounts/${record.id}`)
      setCurrentAccount(normalizeAccount(full))
    } finally {
      setDetailLoading(false)
    }
  }

  const getRefreshToken = (record: any): string => {
    return extractRefreshToken(record)
  }

  const exportCsv = () => {
    const header = 'email,password,status,region,cashier_url,created_at'
    const rows = accounts.map((a) => [a.email, a.password, a.status, a.region, a.cashier_url, a.created_at].join(','))
    const blob = new Blob([[header, ...rows].join('\n')], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${currentPlatform}_accounts.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  const handleDelete = async (id: number) => {
    await apiFetch(`/accounts/${id}`, { method: 'DELETE' })
    message.success('删除成功')
    load()
  }

  const currentPageAccountIds = accounts
    .map((item) => Number(item.id))
    .filter((value) => Number.isInteger(value) && value > 0)

  const getBatchScopeAccountIds = (scope: 'selected' | 'page') => {
    if (scope === 'page') return currentPageAccountIds
    return Array.from(selectedRowKeys)
      .map((value) => Number(value))
      .filter((value) => Number.isInteger(value) && value > 0)
  }

  const handleBatchDelete = async () => {
    if (selectedRowKeys.length === 0) return
    await apiFetch('/accounts/batch-delete', {
      method: 'POST',
      body: JSON.stringify({ ids: Array.from(selectedRowKeys) }),
    })
    message.success('批量删除成功')
    setSelectedRowKeys([])
    load()
  }

  const handleDeleteAccountsByIds = async (ids: number[]) => {
    const validIds = ids.filter((id) => Number.isInteger(id) && id > 0)
    if (validIds.length === 0) return
    await apiFetch('/accounts/batch-delete', {
      method: 'POST',
      body: JSON.stringify({ ids: validIds }),
    })
    message.success(`已删除 ${validIds.length} 个账号`)
    setSelectedRowKeys((prev) => prev.filter((key) => !validIds.includes(Number(key))))
    await load()
  }

  const handleRecoverAccountsByIds = async (ids: number[]) => {
    const validIds = ids.filter((id) => Number.isInteger(id) && id > 0)
    if (validIds.length === 0) return
    await apiFetch('/accounts/batch-recover', {
      method: 'POST',
      body: JSON.stringify({ ids: validIds }),
    })
    message.success(`已恢复 ${validIds.length} 个失败账号`)
    await load()
  }

  const getInvalidAccountIds = async (scope: 'selected' | 'page' | 'all') => {
    const isInvalid = (item: any) => (item.effective_status || item.effectiveStatus || item.status) === 'invalid'
    if (scope === 'selected') {
      return accounts
        .filter((item) => selectedRowKeys.includes(item.id) && isInvalid(item))
        .map((item) => Number(item.id))
    }
    if (scope === 'page') {
      return accounts.filter(isInvalid).map((item) => Number(item.id))
    }
    const data = await apiFetch(
      `/accounts?${new URLSearchParams({
        platform: currentPlatform,
        page: '1',
        page_size: '1000',
        ...(search ? { email: search } : {}),
        status: 'invalid',
      })}`,
    )
    return (data.items || []).map((item: any) => Number(item.id))
  }

  const handleRecoverFailedAccounts = async (scope: 'selected' | 'page' | 'all') => {
    const targetIds = await getInvalidAccountIds(scope)
    if (targetIds.length === 0) {
      message.warning('没有可恢复的失败账号')
      return
    }
    await handleRecoverAccountsByIds(targetIds)
  }

  const handleDeleteFailedAccounts = async (scope: 'selected' | 'page' | 'all') => {
    const targetIds = await getInvalidAccountIds(scope)
    if (targetIds.length === 0) {
      message.warning('没有可删除的失败账号')
      return
    }
    await handleDeleteAccountsByIds(targetIds)
  }

  const handleAdd = async () => {
    const values = await addForm.validateFields()
    await apiFetch('/accounts', {
      method: 'POST',
      body: JSON.stringify({ ...values, platform: currentPlatform }),
    })
    message.success('添加成功')
    setAddModalOpen(false)
    addForm.resetFields()
    load()
  }

  const handleImport = async () => {
    if (!importText.trim()) return
    setImportLoading(true)
    try {
      const lines = importText.trim().split('\n').filter(Boolean)
      const res = await apiFetch('/accounts/import', {
        method: 'POST',
        body: JSON.stringify({ platform: currentPlatform, lines }),
      })
      message.success(`导入成功 ${res.created} 个`)
      setImportModalOpen(false)
      setImportText('')
      load()
    } catch (e: any) {
      message.error(`导入失败: ${e.message}`)
    } finally {
      setImportLoading(false)
    }
  }

  const handleRegister = async () => {
    const values = await registerForm.validateFields()
    setRegisterLoading(true)
    try {
      const cfg = await apiFetch('/config')
      const executorType = normalizeExecutorForPlatform(currentPlatform, cfg.default_executor)
      const registerExtra = buildRegisterExtra(cfg, values)
      const chatgptRegistrationRequestAdapter =
        buildChatGPTRegistrationRequestAdapter(
          currentPlatform,
          chatgptRegistrationMode,
        )
      const adaptedRegisterExtra = chatgptRegistrationRequestAdapter
        ? chatgptRegistrationRequestAdapter.extendExtra(registerExtra)
        : registerExtra

      const submitPayload = {
        platform: currentPlatform,
        count: values.count,
        concurrency: values.concurrency,
        register_delay_seconds: values.register_delay_seconds || 0,
        executor_type: executorType,
        captcha_solver: cfg.default_captcha_solver || 'yescaptcha',
        proxy: null,
        extra: adaptedRegisterExtra,
      }
      const res = await apiFetch('/tasks/register', {
        method: 'POST',
        body: JSON.stringify(submitPayload),
      })
      setTaskId(res.task_id)
      // 同步推送到全局任务上下文：即便切走页面 / 收起 modal，顶部 Badge 也持续可见
      try {
        startGlobalTask({ task_id: res.task_id, total: values.count, status: 'running' })
      } catch {
        /* ignored */
      }
    } finally {
      setRegisterLoading(false)
    }
  }

  const handleDetailSave = async () => {
    const values = await detailForm.validateFields()
    await apiFetch(`/accounts/${currentAccount.id}`, {
      method: 'PATCH',
      body: JSON.stringify(values),
    })
    message.success('保存成功')
    setDetailModalOpen(false)
    load()
  }

  const showBatchActionResult = (title: string, result: any) => {
    const failedItems = (result.items || []).filter((item: any) => !item.ok)
    const failedIds = failedItems
      .map((item: any) => Number(item.id))
      .filter((id: number) => Number.isInteger(id) && id > 0)
    const lines = failedItems.map((item: any) => `[${item.id || '-'}] ${item.email || '-'}: ${item.message || '失败'}`)

    if (lines.length === 0) return

    Modal.info({
      title,
      width: 760,
      okText: failedIds.length > 0 ? `关闭（失败 ${failedIds.length} 个）` : '关闭',
      content: (
        <div>
          {failedIds.length > 0 ? (
            <div style={{ marginBottom: 12, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <Button onClick={() => void handleRecoverAccountsByIds(failedIds)}>
                恢复这些失败账号
              </Button>
              <Button danger icon={<DeleteOutlined />} onClick={() => void handleDeleteAccountsByIds(failedIds)}>
                删除这些失败账号
              </Button>
            </div>
          ) : null}
          <pre
            style={{
              margin: 0,
              maxHeight: 360,
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
            {lines.join('\n')}
          </pre>
        </div>
      ),
    })
  }

  const handleBatchStatusSync = async (kind: 'probe' | 'sub2api', scope: 'selected' | 'page' | 'all') => {
    if (currentPlatform !== 'chatgpt') return

    const loadingKey = `${kind}_${scope}` as typeof statusSyncLoading
    const actionId = kind === 'probe' ? 'probe_local_status' : 'sync_sub2api_status'
    const actionLabel = kind === 'probe' ? '本地探测' : 'Sub2API 状态同步'
    const scopeLabel = scope === 'selected' ? '所选账号' : scope === 'page' ? '当前页账号' : '当前筛选账号'
    const toastKey = `status-sync:${loadingKey}`

    const body: Record<string, unknown> = {
      params: {},
    }

    if (scope === 'selected' || scope === 'page') {
      const accountIds = getBatchScopeAccountIds(scope)
      if (accountIds.length === 0) {
        message.warning('请先选择要同步的账号')
        return
      }
      body.account_ids = accountIds
    } else {
      body.all_filtered = true
      if (search) body.email = search
      if (filterStatus) body.status = filterStatus
    }

    setStatusSyncLoading(loadingKey)
    message.loading({ content: `${scopeLabel}${actionLabel}进行中...`, key: toastKey, duration: 0 })
    try {
      const result = await apiFetch(`/actions/${currentPlatform}/${actionId}/batch`, {
        method: 'POST',
        body: JSON.stringify(body),
      })

      if (!result.total) {
        message.info({ content: '没有可处理的账号', key: toastKey })
      } else if (!result.failed) {
        message.success({ content: `${scopeLabel}${actionLabel}完成：成功 ${result.success} / ${result.total}`, key: toastKey })
      } else if (!result.success) {
        message.error({ content: `${scopeLabel}${actionLabel}失败：成功 ${result.success} / ${result.total}`, key: toastKey })
      } else {
        message.warning({ content: `${scopeLabel}${actionLabel}部分完成：成功 ${result.success} / ${result.total}`, key: toastKey })
      }

      showBatchActionResult(`${scopeLabel}${actionLabel}结果`, result)
      await load()
    } catch (e: any) {
      message.error({ content: `${actionLabel}失败: ${e.message}`, key: toastKey })
    } finally {
      setStatusSyncLoading('')
    }
  }

  const handleBatchSub2ApiUpload = async (scope: 'selected' | 'page' | 'all') => {
    if (currentPlatform !== 'chatgpt') return

    const toastKey = `sub2api-upload:${scope}`
    setSub2ApiUploadLoading(scope)
    message.loading({ content: 'Sub2API 上传中...', key: toastKey, duration: 0 })

    try {
      const cfg = await apiFetch('/config')
      const apiUrl = String(cfg.sub2api_api_url || '').trim()
      const apiKey = String(cfg.sub2api_api_key || '').trim()

      if (!apiUrl) {
        message.warning({ content: '请先在设置中填写 Sub2API API URL。', key: toastKey })
        return
      }

      const body: Record<string, unknown> = {
        params: {
          api_url: apiUrl,
          api_key: apiKey,
        },
      }

      if (scope === 'selected' || scope === 'page') {
        const accountIds = getBatchScopeAccountIds(scope)
        if (accountIds.length === 0) {
          message.warning({ content: '请先选择要上传的账号。', key: toastKey })
          return
        }
        body.account_ids = accountIds
      } else {
        body.all_filtered = true
        if (search) body.email = search
        if (filterStatus) body.status = filterStatus
      }

      const result = await apiFetch(`/actions/${currentPlatform}/upload_sub2api/batch`, {
        method: 'POST',
        body: JSON.stringify(body),
      })

      const scopeLabel = scope === 'selected' ? '所选账号' : scope === 'page' ? '当前页账号' : '当前筛选账号'
      const actionLabel = `${scopeLabel}上传 Sub2API`

      if (!result.total) {
        message.info({ content: '没有可处理的账号。', key: toastKey })
      } else if (!result.failed) {
        message.success({ content: `${actionLabel}完成：${result.success} / ${result.total}`, key: toastKey })
      } else if (!result.success) {
        message.error({ content: `${actionLabel}失败：${result.success} / ${result.total}`, key: toastKey })
      } else {
        message.warning({ content: `${actionLabel}部分完成：${result.success} / ${result.total}`, key: toastKey })
      }

      showBatchActionResult(`${actionLabel}结果`, result)
      await load()
    } catch (e: any) {
      message.error({ content: `Sub2API 上传失败: ${e.message}`, key: toastKey })
    } finally {
      setSub2ApiUploadLoading('')
    }
  }

  const getSelectedCount = () => selectedRowKeys.length
  const getPageCount = () => accounts.length
  const getFailedCountOnPage = () => accounts.filter((item) => (item.effectiveStatus || item.status) === 'invalid').length
  const getFailedSelectedCount = () =>
    accounts.filter((item) => selectedRowKeys.includes(item.id) && (item.effectiveStatus || item.status) === 'invalid').length

  const isChatgptPlatform = currentPlatform === 'chatgpt'
  const monospaceStyle: React.CSSProperties = {
    fontFamily: 'Consolas, Monaco, "Courier New", monospace',
    fontSize: 12,
  }
  const secondaryTextStyle: React.CSSProperties = {
    fontSize: 12,
    color: token.colorTextSecondary,
  }
  const cellStackStyle: React.CSSProperties = {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
    minWidth: 0,
  }
  const secretPreviewStyle: React.CSSProperties = {
    ...monospaceStyle,
    filter: 'blur(3.5px)',
    userSelect: 'none',
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    maxWidth: '100%',
    opacity: 0.8,
  }
  const compactPanelStyle: React.CSSProperties = {
    padding: '8px 10px',
    borderRadius: token.borderRadiusLG,
    border: `1px solid ${token.colorBorder}`,
    background: token.colorFillAlter,
  }

  const columns: any[] = [
    {
      title: '邮箱',
      dataIndex: 'email',
      key: 'email',
      width: 280,
      ellipsis: true,
      render: (text: string, record: any) => (
        <div style={cellStackStyle}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
            <Text
              style={{ ...monospaceStyle, flex: 1, minWidth: 0, whiteSpace: 'nowrap' }}
              ellipsis={{ tooltip: text }}
            >
              {text}
            </Text>
            <Button type="text" size="small" icon={<CopyOutlined />} onClick={() => copyText(text)} />
          </div>
          <Text type="secondary" style={secondaryTextStyle} ellipsis={{ tooltip: record.user_id || `账号 #${record.id}` }}>
            {record.user_id ? `UID: ${record.user_id}` : `账号 #${record.id}`}
          </Text>
        </div>
      ),
    },
    {
      title: '密码',
      dataIndex: 'password',
      key: 'password',
      width: 140,
      ellipsis: true,
      render: (text: string) => (
        <Space size={6} style={{ width: '100%', justifyContent: 'space-between' }}>
          <Text style={{ ...secretPreviewStyle, maxWidth: 80 }} title={text}>
            {text}
          </Text>
          <Button type="text" size="small" icon={<CopyOutlined />} onClick={() => copyText(text)} />
        </Space>
      ),
    },
    {
      title: 'RT',
      key: 'refresh_token',
      width: 110,
      ellipsis: true,
      render: (_: any, record: any) => {
        if (!record.extra_json) {
          return <Tag color={record.has_refresh_token ? 'success' : 'default'}>{record.has_refresh_token ? '有' : '无'}</Tag>
        }
        const rt = getRefreshToken(record)
        if (!rt) return <span style={{ color: '#ccc' }}>-</span>
        return (
          <Space size={6} style={{ width: '100%', justifyContent: 'space-between' }}>
            <Text style={{ ...secretPreviewStyle, fontSize: 11, maxWidth: 50 }} title={rt}>
              {rt}
            </Text>
            <Button type="text" size="small" icon={<CopyOutlined />} onClick={() => copyText(rt)} />
          </Space>
        )
      },
    },
    {
      title: '状态',
      key: 'status',
      width: 120,
      ellipsis: true,
      render: (_: any, record: any) => {
        const es = record.effectiveStatus || record.status || 'registered'
        const reason = record.invalidReason || ''
        const rawStatus = record.status || 'registered'
        const isEffectivelyInvalid = es === 'invalid' && rawStatus !== 'invalid'
        const reasonLabel = INVALID_REASON_LABELS[reason] || reason
        return (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <Tag color={STATUS_COLORS[es] || 'default'}>{es}</Tag>
            {isEffectivelyInvalid && (
              <Text type="secondary" style={{ fontSize: 10, lineHeight: 1.2 }} title={reasonLabel}>
                {reasonLabel || '探测失效'}
              </Text>
            )}
          </div>
        )
      },
    },
  ]

  if (isChatgptPlatform) {
    columns.push(
      {
        title: '本地状态',
        key: 'chatgpt_local_state',
        width: 240,
        ellipsis: true,
        render: (_: any, record: any) => {
          const auth = record.chatgptLocal?.auth || {}
          const subscription = record.chatgptLocal?.subscription || {}
          const codex = record.chatgptLocal?.codex || {}
          const authMeta = authStateMeta(auth.state)
          const planTag = planMeta(subscription.plan)
          const codexMeta = codexStateMeta(codex.state)

          return (
            <div style={{ ...cellStackStyle, ...compactPanelStyle }}>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                <Tag color={authMeta.color}>{authMeta.label}</Tag>
                <Tag color={planTag.color}>{planTag.label}</Tag>
                <Tag color={codexMeta.color}>Codex {codexMeta.label}</Tag>
              </div>
            </div>
          )
        },
      },
      {
        title: '支付状态',
        key: 'auto_pay_state',
        width: 130,
        ellipsis: true,
        render: (_: any, record: any) => {
          const payState = record.autoPayState
          if (!payState) return <span style={{ color: '#666' }}>-</span>
          const meta = paymentStateMeta(payState)
          const diagCode = record.autoPayDiag || ''
          const diagLabel = DIAG_LABELS[diagCode] || diagCode
          const provider = record.autoPayProvider || ''
          const plan = record.autoPayPlan || ''
          return (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <Tag color={meta.color}>{meta.label}</Tag>
              {provider && <Text type="secondary" style={{ fontSize: 10 }}>{PROVIDER_LABELS[provider] || provider}{plan ? `/${plan}` : ''}</Text>}
              {diagCode && meta.color === 'error' && (
                <Text type="danger" style={{ fontSize: 10, lineHeight: 1.2 }} title={diagCode}>
                  {diagLabel}
                </Text>
              )}
            </div>
          )
        },
      },
      {
        title: 'Sub2API',
        key: 'sub2api_sync',
        width: 160,
        ellipsis: true,
        render: (_: any, record: any) => {
          const sync = record.sub2apiSync || {}
          const meta = sub2ApiStateMeta(sync)

          return (
            <div style={{ ...cellStackStyle, ...compactPanelStyle }}>
              <Tag color={meta.color}>{meta.label}</Tag>
            </div>
          )
        },
      },
      {
        title: 'Plus 长链',
        dataIndex: 'cashier_url',
        key: 'cashier_url',
        width: 110,
        ellipsis: true,
        render: (url: string) => <PaymentLinkCell url={url} />,
      },
    )
  } else {
    columns.push(
      {
        title: '地区',
        dataIndex: 'region',
        key: 'region',
        width: 100,
        ellipsis: true,
        render: (text: string) => text || '-',
      },
      {
        title: '试用链接',
        dataIndex: 'cashier_url',
        key: 'cashier_url',
        width: 110,
        ellipsis: true,
        render: (url: string) => <PaymentLinkCell url={url} />,
      },
    )
  }

  columns.push(
    {
      title: '注册时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 140,
      ellipsis: true,
      render: (text: string) => {
        const formatted = formatCreatedAt(text)
        return (
          <div style={cellStackStyle}>
            <Text style={{ fontSize: 13 }}>{formatted.date}</Text>
            {formatted.time ? <Text type="secondary" style={secondaryTextStyle}>{formatted.time}</Text> : null}
          </div>
        )
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 140,
      fixed: isChatgptPlatform ? 'right' : undefined,
      render: (_: any, record: any) => (
        <Space size={4} wrap>
          <Button type="link" size="small" onClick={() => { void openAccountDetail(record) }}>
            详情
          </Button>
          <Popconfirm title="确认删除？" onConfirm={() => handleDelete(record.id)}>
            <Button type="link" size="small" danger>
              删除
            </Button>
          </Popconfirm>
          <ActionMenu acc={record} onRefresh={load} actions={platformActions} />
        </Space>
      ),
    },
  )

  const visibleColumns = columns
    .filter((column) => {
      if (['password', 'created_at'].includes(String(column.key))) return false
      if (viewMode === 'table-dense') return true
      if (viewMode === 'table-compact') {
        return !['auto_pay_state'].includes(String(column.key))
      }
      return false
    })
    .map((column) => {
      if (String(column.key) !== 'action') return column
      return {
        ...column,
        width: viewMode === 'table-dense' ? 150 : 128,
        fixed: isChatgptPlatform && viewMode === 'table-dense' ? 'right' : undefined,
        render: (_: any, record: any) => (
          <div className="account-row-actions">
            <Button className="account-action-primary" type="primary" size="small" onClick={() => { void openAccountDetail(record) }}>
              详情
            </Button>
            <ActionMenu acc={record} onRefresh={load} actions={platformActions} onDelete={() => handleDelete(record.id)} />
          </div>
        ),
      }
    })

  const tableScrollX = isChatgptPlatform
    ? viewMode === 'table-dense'
      ? 1520
      : 1080
    : 1000

  const drawerWidth = screens.xl ? 780 : screens.lg ? 680 : '100%'

  const statusSyncMenuItems: MenuProps['items'] = [
    {
      key: 'probe:selected',
      label: `探测所选账号 (${getSelectedCount()})`,
      disabled: getSelectedCount() === 0,
    },
    {
      key: 'probe:page',
      label: `探测当前页 (${getPageCount()})`,
      disabled: getPageCount() === 0,
    },
    {
      key: 'probe:all',
      label: `探测当前筛选全部 (${total})`,
      disabled: total === 0,
    },
    {
      key: 'sub2api:selected',
      label: `同步所选 Sub2API 状态 (${getSelectedCount()})`,
      disabled: getSelectedCount() === 0,
    },
    {
      key: 'sub2api:page',
      label: `同步当前页 Sub2API 状态 (${getPageCount()})`,
      disabled: getPageCount() === 0,
    },
    {
      key: 'sub2api:all',
      label: `同步当前筛选全部 Sub2API 状态 (${total})`,
      disabled: total === 0,
    },
  ]

  return (
    <div className="page-container accounts-page">
      <div className="accounts-toolbar">
        <div className="accounts-toolbar__primary">
          <Input.Search
            placeholder="搜索邮箱..."
            allowClear
            onSearch={setSearch}
            className="accounts-toolbar__search"
          />
          <Select
            placeholder="状态筛选"
            allowClear
            className="accounts-toolbar__filter"
            onChange={setFilterStatus}
            options={[
              { value: 'group:success', label: '正常账号' },
              { value: 'group:failed', label: '全部失效' },
              { value: 'registered', label: '已注册' },
              { value: 'trial', label: '试用中' },
              { value: 'subscribed', label: '已订阅' },
              { value: 'expired', label: '已过期' },
              { value: 'invalid', label: '已失效' },
            ]}
          />
          <Text type="secondary">{total} 个账号</Text>
          {selectedRowKeys.length > 0 && (
            <Text type="success">已选 {selectedRowKeys.length} 个</Text>
          )}
        </div>
        <div className="accounts-toolbar__actions">
          {currentPlatform === 'chatgpt' && (
            <Dropdown
              trigger={['click']}
              menu={{
                items: [
                  { key: 'selected', label: `恢复所选失败账号 (${getFailedSelectedCount()})`, disabled: getFailedSelectedCount() === 0 },
                  { key: 'page', label: `恢复当前页失败账号 (${getFailedCountOnPage()})`, disabled: getFailedCountOnPage() === 0 },
                  { key: 'all', label: '恢复当前筛选失败账号', disabled: total === 0 },
                ],
                onClick: ({ key }) => void handleRecoverFailedAccounts(String(key) as 'selected' | 'page' | 'all'),
              }}
            >
              <Button disabled={total === 0}>
                恢复失败账号
              </Button>
            </Dropdown>
          )}
          {currentPlatform === 'chatgpt' && (
            <Dropdown
              trigger={['click']}
              menu={{
                items: [
                  { key: 'selected', label: `删除所选失败账号 (${getFailedSelectedCount()})`, disabled: getFailedSelectedCount() === 0 },
                  { key: 'page', label: `删除当前页失败账号 (${getFailedCountOnPage()})`, disabled: getFailedCountOnPage() === 0 },
                  { key: 'all', label: '删除当前筛选失败账号', disabled: total === 0 },
                ],
                onClick: ({ key }) => void handleDeleteFailedAccounts(String(key) as 'selected' | 'page' | 'all'),
              }}
            >
              <Button danger disabled={total === 0}>
                删除失败账号
              </Button>
            </Dropdown>
          )}
          {currentPlatform === 'chatgpt' && (
            <Dropdown
              trigger={['click']}
              menu={{
                items: statusSyncMenuItems,
                onClick: ({ key }) => {
                  const [kind, scope] = String(key).split(':') as ['probe' | 'sub2api', 'selected' | 'page' | 'all']
                  handleBatchStatusSync(kind, scope)
                },
              }}
            >
              <Button
                icon={<SyncOutlined />}
                loading={statusSyncLoading !== ''}
                disabled={total === 0}
              >
                状态同步
              </Button>
            </Dropdown>
          )}
          {currentPlatform === 'chatgpt' && (
            <Dropdown
              trigger={['click']}
              menu={{
                items: [
                  { key: 'selected', label: `上传所选到 Sub2API (${getSelectedCount()})`, disabled: getSelectedCount() === 0 },
                  { key: 'page', label: `上传当前页到 Sub2API (${getPageCount()})`, disabled: getPageCount() === 0 },
                  { key: 'all', label: `上传当前筛选全部到 Sub2API (${total})`, disabled: total === 0 },
                ],
                onClick: ({ key }) => handleBatchSub2ApiUpload(String(key) as 'selected' | 'page' | 'all'),
              }}
            >
              <Button
                icon={<UploadOutlined />}
                loading={sub2ApiUploadLoading !== ''}
                disabled={total === 0}
              >
                上传 Sub2API
              </Button>
            </Dropdown>
          )}
          {selectedRowKeys.length > 0 && (
            <Popconfirm title={`确认删除选中的 ${selectedRowKeys.length} 个账号？`} onConfirm={handleBatchDelete}>
              <Button danger icon={<DeleteOutlined />}>删除 {selectedRowKeys.length} 个</Button>
            </Popconfirm>
          )}
          <Button icon={<UploadOutlined />} onClick={() => setImportModalOpen(true)}>导入</Button>
          <Button icon={<DownloadOutlined />} onClick={exportCsv} disabled={accounts.length === 0}>导出</Button>
          <Button icon={<PlusOutlined />} onClick={() => setAddModalOpen(true)}>新增</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setRegisterModalOpen(true)}>注册</Button>
          <Button icon={<ReloadOutlined spin={loading} />} onClick={load} />
        </div>
      </div>

      <Card bordered={false} className={`data-table-card accounts-table-card accounts-table-card--${viewMode}`}>
      <div className="accounts-table-wrap" ref={tableContainerRef}>
      {viewMode === 'card-list' ? (
        <>
          <div className="accounts-card-grid">
            {accounts.map((account) => (
              <AccountCard
                key={account.id}
                account={account}
                selected={selectedRowKeys.includes(account.id)}
                onToggleSelect={(checked) => {
                  setSelectedRowKeys((prev) => {
                    const exists = prev.includes(account.id)
                    if (checked && !exists) return [...prev, account.id]
                    if (!checked && exists) return prev.filter((key) => key !== account.id)
                    return prev
                  })
                }}
                onOpenDetail={() => { void openAccountDetail(account) }}
                onDelete={() => { void handleDelete(account.id) }}
                actions={platformActions}
                onRefresh={load}
              />
            ))}
          </div>
          <div className="accounts-card-pagination">
            <Pagination
              current={page}
              pageSize={pageSize}
              total={total}
              showSizeChanger
              pageSizeOptions={['20', '50', '100']}
              onChange={(nextPage, nextSize) => {
                setPage(nextPage)
                if (nextSize && nextSize !== pageSize) {
                  setPageSize(nextSize)
                  try {
                    localStorage.setItem('accounts_page_size', String(nextSize))
                  } catch {
                    /* ignored */
                  }
                }
              }}
            />
          </div>
        </>
      ) : (
      <Table
        rowKey="id"
        columns={visibleColumns}
        dataSource={accounts}
        loading={loading}
        size="middle"
        rowSelection={{
          selectedRowKeys,
          onChange: setSelectedRowKeys,
        }}
            pagination={{
              current: page,
              pageSize,
              total,
              position: ['topRight'],
              showSizeChanger: true,
              showQuickJumper: true,
              pageSizeOptions: ['20', '50', '100'],
          showTotal: (t, range) => `第 ${range[0]}-${range[1]} 条 / 共 ${t} 个账号`,
          onChange: (nextPage, nextSize) => {
            setPage(nextPage)
            if (nextSize && nextSize !== pageSize) {
              setPageSize(nextSize)
              try {
                localStorage.setItem('accounts_page_size', String(nextSize))
              } catch {
                /* ignored */
              }
            }
          },
          onShowSizeChange: (_cur, nextSize) => {
            setPage(1)
            setPageSize(nextSize)
            try {
              localStorage.setItem('accounts_page_size', String(nextSize))
            } catch {
              /* ignored */
            }
          },
        }}
        scroll={{ x: tableScrollX }}
        onRow={(record) => ({
          onDoubleClick: () => { void openAccountDetail(record) },
        })}
      />
      )}
      </div>
      </Card>

      {/* 收起态：用固定定位的小卡片代替 Modal，不阻挡页面交互 */}
      {registerModalOpen && registerModalCollapsed && taskId && (() => {
        const p = taskMeta?.progress?.split('/') || []
        const total = Number(taskMeta?.total || 0) || parseInt(p[1], 10) || 0
        const done = Number(taskMeta?.completed || 0) || parseInt(p[0], 10) || 0
        const started = Number(taskMeta?.started || 0)
        const success = Number(taskMeta?.success || 0)
        const skipped = Number(taskMeta?.skipped || 0)
        const failed = Math.max(0, done - success - skipped)
        const pending = Math.max(0, total - done)
        const percent = total > 0 ? Math.round((done / total) * 100) : 0
        const isTaskDone = taskMeta?.status === 'done' || taskMeta?.status === 'failed' || taskMeta?.status === 'stopped'
        return (
        <div
          className="floating-task-card"
          style={{
            position: 'fixed', right: 24, bottom: 24, zIndex: 1000,
            width: 360, padding: '16px 18px',
            background: 'linear-gradient(135deg, #1e293b 0%, #0f172a 100%)',
            borderRadius: 14,
            boxShadow: '0 8px 32px rgba(0,0,0,.25), 0 2px 8px rgba(0,0,0,.15)',
            border: '1px solid rgba(255,255,255,.08)',
          }}
        >
          {/* 标题栏 */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              {isTaskDone
                ? (taskMeta?.status === 'done'
                  ? <CheckCircleOutlined style={{ color: '#4ade80', fontSize: 14 }} />
                  : <ExclamationCircleOutlined style={{ color: '#f87171', fontSize: 14 }} />)
                : <LoadingOutlined spin style={{ fontSize: 14, color: '#3b82f6' }} />}
              <span style={{ fontWeight: 600, fontSize: 13, color: '#e2e8f0' }}>
                注册 {currentPlatform}
              </span>
            </div>
            <Space size={4}>
              <Button size="small" type="text"
                icon={<DownOutlined />}
                style={{ color: '#94a3b8', fontSize: 12 }}
                onClick={() => setRegisterModalCollapsed(false)}>
                展开
              </Button>
              {false ? (
                <Button size="small" type="text" style={{ color: '#f87171', fontSize: 12 }} onClick={() => { setRegisterModalOpen(false); setRegisterModalCollapsed(false); setTaskId(null); setTaskMeta(null); registerForm.resetFields(); }}>
                  关闭
                </Button>
              ) : (
                <Popconfirm
                  title="关闭任务窗"
                  description="任务仍在后台运行，关闭后可在后台继续。确认关闭？"
                  onConfirm={() => {
                    setRegisterModalOpen(false); setRegisterModalCollapsed(false); setTaskId(null); setTaskMeta(null); registerForm.resetFields()
                  }}
                  okText="确认关闭"
                  cancelText="取消"
                >
                  <Button size="small" type="text" style={{ color: '#f87171', fontSize: 12 }}>
                    关闭
                  </Button>
                </Popconfirm>
              )}
            </Space>
          </div>

          {/* 进度条 */}
          {total > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                <span style={{ fontSize: 20, fontWeight: 700, color: '#e2e8f0', lineHeight: 1 }}>
                  {done}<span style={{ fontSize: 13, fontWeight: 400, color: '#64748b' }}>/{total}</span>
                </span>
                <span style={{ fontSize: 13, fontWeight: 600, color: percent === 100 ? '#4ade80' : '#60a5fa' }}>
                  {percent}%
                </span>
              </div>
              <div style={{
                height: 6, borderRadius: 3,
                background: 'rgba(255,255,255,.08)',
                overflow: 'hidden',
              }}>
                <div style={{
                  height: '100%', borderRadius: 3,
                  width: `${percent}%`,
                  background: percent === 100
                    ? (failed > 0 ? 'linear-gradient(90deg, #f59e0b, #f97316)' : 'linear-gradient(90deg, #22c55e, #4ade80)')
                    : 'linear-gradient(90deg, #3b82f6, #60a5fa)',
                  transition: 'width .6s cubic-bezier(.4,0,.2,1)',
                }} />
              </div>
            </div>
          )}

          {/* 数据格子 */}
          <div style={{
            display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 8,
          }}>
            {[
              { label: '成功', value: success, icon: <CheckCircleOutlined />, color: '#4ade80', bg: 'rgba(34,197,94,.1)' },
              { label: '失败', value: failed, icon: <CloseCircleOutlined />, color: '#f87171', bg: 'rgba(239,68,68,.1)' },
              { label: '待完成', value: pending, icon: <ClockCircleOutlined />, color: '#60a5fa', bg: 'rgba(59,130,246,.1)' },
              { label: '已启动', value: started, icon: <ExclamationCircleOutlined />, color: '#fbbf24', bg: 'rgba(251,191,36,.1)' },
            ].map((item) => (
              <div key={item.label} style={{
                textAlign: 'center', padding: '8px 4px',
                background: item.bg, borderRadius: 8,
                border: '1px solid rgba(255,255,255,.04)',
              }}>
                <div style={{ fontSize: 18, fontWeight: 700, color: item.color, lineHeight: 1.2 }}>
                  {item.value}
                </div>
                <div style={{ fontSize: 10, color: '#64748b', marginTop: 3, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 3 }}>
                  {item.icon} {item.label}
                </div>
              </div>
            ))}
          </div>

          {/* 状态文字 */}
          {isTaskDone ? (
            <div 
              onClick={() => { closeRegisterTaskPanel() }}
              style={{
              marginTop: 10, textAlign: 'center', fontSize: 12, fontWeight: 600, padding: '5px 0', borderRadius: 6,
              color: taskMeta?.status === 'done' ? '#4ade80' : taskMeta?.status === 'failed' ? '#f87171' : '#fbbf24',
              background: taskMeta?.status === 'done' ? 'rgba(34,197,94,.08)' : taskMeta?.status === 'failed' ? 'rgba(239,68,68,.08)' : 'rgba(251,191,36,.08)',
              cursor: 'pointer'
            }}>
              {taskMeta?.status === 'done' ? '✓ 任务完成 (点击关闭)' : taskMeta?.status === 'failed' ? '✗ 任务失败 (点击关闭)' : '■ 已停止 (点击关闭)'}
            </div>
          ) : (
            <div style={{ marginTop: 10, textAlign: 'center', fontSize: 11, color: '#475569' }}>
              任务进行中
            </div>
          )}
        </div>
        )
      })()}

      <Modal
        title={(
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
            <span>注册 {currentPlatform}</span>
            {taskId ? (
              <Button
                size="small"
                type="text"
                icon={registerModalCollapsed ? <DownOutlined /> : <UpOutlined />}
                onClick={() => setRegisterModalCollapsed((value) => !value)}
              >
                {registerModalCollapsed ? '展开' : '收起'}
              </Button>
            ) : null}
          </div>
        )}
        open={registerModalOpen && !registerModalCollapsed}
        onCancel={() => {
          if (taskId) {
            setRegisterModalCollapsed(true)
          } else {
            setRegisterModalOpen(false)
            setRegisterModalCollapsed(false)
            setTaskId(null)
            registerForm.resetFields()
          }
        }}
        footer={null}
        width={820}
        maskClosable={false}
        destroyOnHidden={false}
        style={{ top: 24 }}
      >
        {!taskId ? (
          <Form
            form={registerForm}
            layout="vertical"
            onFinish={handleRegister}
            initialValues={{
              count: 1,
              concurrency: 1,
              register_delay_seconds: 0,
              mail_provider: 'luckmail',
              mail_config_override_enabled: false,
              mail_provider_mix_enabled: false,
              mail_provider_mix: DEFAULT_PARALLEL_MAIL_MIX,
            }}
          >
            <Form.Item name="count" label="注册数量" rules={[{ required: true }]}>
              <Input type="number" min={1} />
            </Form.Item>
            <Form.Item name="concurrency" label="并发数" rules={[{ required: true }]}>
              <Input type="number" min={1} max={50} />
            </Form.Item>
            <Form.Item name="register_delay_seconds" label="每个注册延迟(秒)">
              <InputNumber min={0} precision={1} step={0.5} style={{ width: '100%' }} placeholder="0 = 不延迟" />
            </Form.Item>
            {currentPlatform === 'chatgpt' && (
              <Form.Item name="smsbower_add_phone_send_attempts" label="add-phone 发送次数">
                <Input placeholder="最大尝试次数 / 8" />
              </Form.Item>
            )}
            <Form.Item name="mail_provider" label="邮箱服务" rules={[{ required: true }]}>
              <Select
                options={[
                  { value: 'luckmail', label: 'LuckMail' },
                  { value: 'mail2925', label: '2925 Mail' },
                  { value: 'cfworker', label: 'CF Worker' },
                  { value: 'maliapi', label: 'MaliAPI' },
                  { value: 'gptmail', label: 'GPTMail' },
                  { value: 'skymail', label: 'SkyMail' },
                  { value: 'duckmail', label: 'DuckMail' },
                  { value: 'duckduckgo', label: 'DuckDuckGo' },
                  { value: 'freemail', label: 'Freemail' },
                  { value: 'moemail', label: 'MoeMail' },
                  { value: 'opentrashmail', label: 'OpenTrashMail' },
                  { value: 'laoudo', label: 'Laoudo' },
                  { value: 'tempmail_lol', label: 'TempMail.lol' },
                ]}
              />
            </Form.Item>
            <Form.Item name="mail_provider_mix_enabled" hidden>
              <Input />
            </Form.Item>
            <Form.Item name="mail_config_override_enabled" hidden>
              <Input />
            </Form.Item>
            <Form.Item style={{ marginBottom: 12 }}>
              <Checkbox
                checked={!!registerMailProviderMixEnabled}
                onChange={(e: CheckboxChangeEvent) => {
                  registerForm.setFieldValue('mail_provider_mix_enabled', e.target.checked)
                  if (e.target.checked) {
                    registerForm.setFieldValue('mail_provider_mix', mixProviderOptions.map((item) => item.value))
                  }
                }}
              >
                启用并行邮箱混用
              </Checkbox>
            </Form.Item>
            <Form.Item style={{ marginBottom: 12 }}>
              <Checkbox
                checked={!!registerMailConfigOverrideEnabled}
                onChange={(e: CheckboxChangeEvent) => {
                  registerForm.setFieldValue('mail_config_override_enabled', e.target.checked)
                }}
              >
                临时覆盖邮箱配置
              </Checkbox>
            </Form.Item>
            {!registerMailConfigOverrideEnabled && (
              <Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>
                默认直接使用全局设置里已保存的邮箱配置，不需要重复填写。
              </Text>
            )}
            {registerMailProviderMixEnabled && (
              <Form.Item
                name="mail_provider_mix"
                label="混用邮箱池"
                rules={[
                  {
                    validator: (_, value) =>
                      Array.isArray(value) && value.length > 0
                        ? Promise.resolve()
                        : Promise.reject(new Error('请至少勾选一个邮箱源')),
                  },
                ]}
              >
                <Checkbox.Group
                  options={mixProviderOptions}
                />
              </Form.Item>
            )}
            {registerMailConfigOverrideEnabled && selectedRegisterMailProviders.includes('mail2925') && (
              <>
                <Form.Item name="mail2925_login_name" label="2925 Login Name">
                  <Input placeholder="yourname" />
                </Form.Item>
                <Form.Item name="mail2925_password" label="2925 Password">
                  <Input.Password placeholder="password" />
                </Form.Item>
                <Form.Item name="mail2925_alias_mode" label="2925 Alias Mode">
                  <Input placeholder="main / plus / random" />
                </Form.Item>
                <Form.Item name="mail2925_domain" label="2925 Domain">
                  <Input placeholder="2925.com" />
                </Form.Item>
              </>
            )}
            {registerMailConfigOverrideEnabled && selectedRegisterMailProviders.includes('cfworker') && (
              <>
                <Form.Item name="cfworker_api_url" label="CF Worker API URL">
                  <Input placeholder="https://apimail.example.com" />
                </Form.Item>
                <Form.Item name="cfworker_admin_token" label="CF Worker Admin Token">
                  <Input placeholder="abc123,,,abc" />
                </Form.Item>
                <Form.Item name="cfworker_custom_auth" label="CF Worker Site Password">
                  <Input.Password placeholder="private site password" />
                </Form.Item>
              </>
            )}
            {registerMailConfigOverrideEnabled && selectedRegisterMailProviders.includes('luckmail') && (
              <>
                <Form.Item name="luckmail_base_url" label="LuckMail Base URL">
                  <Input placeholder="https://mails.luckyous.com/" />
                </Form.Item>
                <Form.Item name="luckmail_api_key" label="LuckMail API Key">
                  <Input.Password placeholder="ak_..." />
                </Form.Item>
              </>
            )}
            {currentPlatform === 'chatgpt' && (
              <Form.Item label="ChatGPT Token 方案">
                <ChatGPTRegistrationModeSwitch
                  mode={chatgptRegistrationMode}
                  onChange={setChatgptRegistrationMode}
                />
              </Form.Item>
            )}
            <Form.Item>
              <Button type="primary" htmlType="submit" block loading={registerLoading}>
                开始注册
              </Button>
            </Form.Item>
          </Form>
        ) : (
          <TaskLogPanel taskId={taskId} taskMeta={taskMeta || undefined} onDone={() => { load(); closeRegisterTaskPanel(); }} />
        )}
      </Modal>

      <Modal
        title="手动新增账号"
        open={addModalOpen}
        onCancel={() => { setAddModalOpen(false); addForm.resetFields(); }}
        onOk={handleAdd}
        maskClosable={false}
      >
        <Form form={addForm} layout="vertical">
          <Form.Item name="email" label="邮箱" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item name="token" label="Token">
            <Input />
          </Form.Item>
          <Form.Item name="cashier_url" label="试用链接">
            <Input />
          </Form.Item>
          <Form.Item name="status" label="状态" initialValue="registered">
            <Select
              options={[
                { value: 'registered', label: '已注册' },
                { value: 'trial', label: '试用中' },
                { value: 'subscribed', label: '已订阅' },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="批量导入"
        open={importModalOpen}
        onCancel={() => { setImportModalOpen(false); setImportText(''); }}
        onOk={handleImport}
        confirmLoading={importLoading}
        maskClosable={false}
      >
        <p style={{ marginBottom: 8, fontSize: 12, color: '#7a8ba3' }}>
          每行格式: <code style={{ background: 'rgba(255,255,255,0.1)', padding: '2px 4px', borderRadius: 4 }}>email password [cashier_url]</code>
        </p>
        <Input.TextArea
          value={importText}
          onChange={(e) => setImportText(e.target.value)}
          rows={8}
          style={{ fontFamily: 'monospace' }}
        />
      </Modal>

      <Modal
        title="账号详情"
        open={false && detailModalOpen}
        onCancel={() => setDetailModalOpen(false)}
        onOk={handleDetailSave}
        confirmLoading={detailLoading}
        maskClosable={false}
        width={760}
        styles={{ body: { maxHeight: '72vh', overflowY: 'auto' } }}
      >
        {currentAccount && (
          <>
            {currentAccount.effectiveStatus === 'invalid' && currentAccount.status !== 'invalid' && (
              <Alert
                type="warning"
                showIcon
                style={{ marginBottom: 12 }}
                message={`探测判定失效：${INVALID_REASON_LABELS[currentAccount.invalidReason] || currentAccount.invalidReason || '未知原因'}`}
                description="数据库状态未标记为失效，但本地探测或远端同步检测到此账号已不可用。"
              />
            )}
            <Form form={detailForm} layout="vertical" initialValues={currentAccount}>
              <Form.Item name="status" label="状态">
                <Select
                  options={[
                    { value: 'registered', label: '已注册' },
                    { value: 'trial', label: '试用中' },
                    { value: 'subscribed', label: '已订阅' },
                    { value: 'expired', label: '已过期' },
                    { value: 'invalid', label: '已失效' },
                  ]}
                />
              </Form.Item>
              <Form.Item name="token" label="Access Token">
                <Input.TextArea rows={2} style={{ fontFamily: 'monospace' }} />
              </Form.Item>
              <Form.Item name="cashier_url" label="Plus 长链">
                <Input.TextArea rows={2} style={{ fontFamily: 'monospace' }} />
              </Form.Item>
            </Form>
            {(() => {
              const rt = getRefreshToken(currentAccount)
              if (!rt) return null
              return (
                <div style={{ marginTop: 8 }}>
                  <div style={{ marginBottom: 4, fontWeight: 500, fontSize: 13 }}>Refresh Token</div>
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'flex-start',
                      gap: 8,
                      background: token.colorFillAlter,
                      border: `1px solid ${token.colorBorder}`,
                      borderRadius: token.borderRadius,
                      padding: '8px 10px',
                    }}
                  >
                    <Text
                      style={{ fontFamily: 'monospace', fontSize: 11, wordBreak: 'break-all', flex: 1, userSelect: 'text' }}
                      copyable={{ text: rt, tooltips: ['复制 RT', '已复制'] }}
                    >
                      {rt}
                    </Text>
                  </div>
                </div>
              )
            })()}
            {currentPlatform === 'chatgpt' ? (
              <DetailSection title="本地真实状态">
                {currentAccount.chatgptLocal && Object.keys(currentAccount.chatgptLocal).length > 0 ? (
                  <LocalProbeSummary probe={currentAccount.chatgptLocal} />
                ) : (
                  <Text type="secondary">尚未探测。可在操作菜单中点击“探测本地状态”。</Text>
                )}
              </DetailSection>
            ) : null}
            {currentPlatform === 'chatgpt' ? (
              <DetailSection title="Sub2API 状态">
                {currentAccount.sub2apiSync && Object.keys(currentAccount.sub2apiSync).length > 0 ? (
                  <Sub2ApiSyncSummary sync={currentAccount.sub2apiSync} />
                ) : (
                  <Text type="secondary">尚未同步。可在操作菜单中点击“同步 Sub2API 状态”。</Text>
                )}
              </DetailSection>
            ) : null}
          </>
        )}
      </Modal>
      <Drawer
        title="账号详情"
        open={detailModalOpen}
        onClose={() => setDetailModalOpen(false)}
        destroyOnHidden={false}
        maskClosable={false}
        width={drawerWidth}
        styles={{ body: { paddingBottom: 120 } }}
        footer={
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
            <Button onClick={() => setDetailModalOpen(false)}>关闭</Button>
            <Button type="primary" loading={detailLoading} onClick={() => void handleDetailSave()}>
              保存
            </Button>
          </div>
        }
      >
        {currentAccount && (
          <>
            {currentAccount.effectiveStatus === 'invalid' && currentAccount.status !== 'invalid' ? (
              <Alert
                type="warning"
                showIcon
                className="accounts-detail-alert"
                message={`探测判定失效：${INVALID_REASON_LABELS[currentAccount.invalidReason] || currentAccount.invalidReason || '未知原因'}`}
                description="数据库状态未标记为失效，但本地探测或远端同步检测到此账号已不可用。"
              />
            ) : null}
            <div className="accounts-detail-hero">
              <div className="accounts-detail-hero__main">
                <Text className="mono-text" ellipsis={{ tooltip: currentAccount.email }}>
                  {currentAccount.email}
                </Text>
                <Text type="secondary" ellipsis={{ tooltip: currentAccount.user_id || `账号 #${currentAccount.id}` }}>
                  {currentAccount.user_id ? `UID: ${currentAccount.user_id}` : `账号 #${currentAccount.id}`}
                </Text>
              </div>
              <div className="accounts-detail-hero__meta">
                <Tag color={statusTagMeta(currentAccount.effectiveStatus || currentAccount.status).color}>
                  {statusTagMeta(currentAccount.effectiveStatus || currentAccount.status).label}
                </Tag>
                {currentPlatform === 'chatgpt' ? (
                  <>
                    <CompactStatusTags
                      authState={currentAccount.chatgptLocal?.auth?.state}
                      plan={currentAccount.chatgptLocal?.subscription?.plan}
                      codexState={currentAccount.chatgptLocal?.codex?.state}
                    />
                    <Tag color={sub2ApiStateMeta(currentAccount.sub2apiSync || {}).color}>
                      Sub2API {sub2ApiStateMeta(currentAccount.sub2apiSync || {}).label}
                    </Tag>
                  </>
                ) : null}
              </div>
            </div>
            <div className="accounts-detail-grid">
              <DetailSection title="基础信息">
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  <SummaryField label="邮箱" value={currentAccount.email} />
                  <SummaryField label="UID" value={currentAccount.user_id || `账号 #${currentAccount.id}`} />
                  <SummaryField label="密码" value={currentAccount.password} code />
                  <SummaryField label="创建时间" value={formatSyncTime(currentAccount.created_at)} />
                </div>
              </DetailSection>
              <DetailSection title="账号设置">
                <Form form={detailForm} layout="vertical" initialValues={currentAccount}>
                  <Form.Item name="status" label="状态">
                    <Select
                      options={[
                        { value: 'registered', label: '已注册' },
                        { value: 'trial', label: '试用中' },
                        { value: 'subscribed', label: '已订阅' },
                        { value: 'expired', label: '已过期' },
                        { value: 'invalid', label: '已失效' },
                      ]}
                    />
                  </Form.Item>
                  <Form.Item name="token" label="Access Token">
                    <Input.TextArea rows={3} style={{ fontFamily: 'monospace' }} />
                  </Form.Item>
                  <Form.Item name="cashier_url" label="Plus 长链">
                    <Input.TextArea rows={3} style={{ fontFamily: 'monospace' }} />
                  </Form.Item>
                </Form>
              </DetailSection>
            </div>
            {currentPlatform === 'chatgpt' ? (
              <DetailSection title="本地状态">
                {currentAccount.chatgptLocal && Object.keys(currentAccount.chatgptLocal).length > 0 ? (
                  <LocalProbeSummary probe={currentAccount.chatgptLocal} />
                ) : (
                  <Text type="secondary">还没有本地探测结果。</Text>
                )}
              </DetailSection>
            ) : null}
            {currentPlatform === 'chatgpt' ? (
              <DetailSection title="Sub2API 状态">
                {currentAccount.sub2apiSync && Object.keys(currentAccount.sub2apiSync).length > 0 ? (
                  <Sub2ApiSyncSummary sync={currentAccount.sub2apiSync} />
                ) : (
                  <Text type="secondary">还没有 Sub2API 同步结果。</Text>
                )}
              </DetailSection>
            ) : null}
          </>
        )}
      </Drawer>
    </div>
  )
}
