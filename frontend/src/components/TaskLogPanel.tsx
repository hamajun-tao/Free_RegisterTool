import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Button, Collapse, Progress, message, Space, Tooltip } from 'antd'
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  CodeOutlined,
  CopyOutlined,
  ExclamationCircleOutlined,
  FastForwardOutlined,
  ForwardOutlined,
  LoadingOutlined,
  MinusCircleOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  StopOutlined,
} from '@ant-design/icons'

import { API_BASE, apiFetch, getToken } from '@/lib/utils'

const MAX_CLIENT_LOG_LINES = 2000
const STEP_ANALYSIS_LOG_LINES = 300

// ═══════════════════════════════════════════════════════════════════════════
// 步骤定义：将后端日志行解析为用户可读的步骤
// ═══════════════════════════════════════════════════════════════════════════

interface StepDef {
  id: string
  label: string
  triggerStart: (line: string) => boolean
  triggerDone: (line: string) => boolean
  triggerFail: (line: string) => boolean
  extractDetail?: (line: string) => string | null
}

const STEP_DEFS: StepDef[] = [
  {
    id: 'ip',
    label: '检查网络环境',
    triggerStart: (l) => l.includes('检查 IP 地理位置') || l.includes('check_ip'),
    triggerDone: (l) => l.includes('IP 位置:') || l.includes('Session 指纹已初始化'),
    triggerFail: (l) => l.includes('IP 地理位置检查失败'),
    extractDetail: (l) => {
      const m = l.match(/IP 位置:\s*(\S+)/)
      return m ? `地区: ${m[1]}` : null
    },
  },
  {
    id: 'email',
    label: '创建邮箱',
    triggerStart: (l) => l.includes('正在创建第') && l.includes('个邮箱'),
    triggerDone: (l) => l.includes('邮箱创建成功') || l.includes('成功创建邮箱'),
    triggerFail: (l) => l.includes('邮箱创建失败') || l.includes('邮箱创建重试耗尽'),
    extractDetail: (l) => {
      const email = l.match(/成功创建邮箱:\s*(\S+)/)
      if (email) return email[1]
      const provider = l.match(/provider=([^\s]+)/)
      return provider ? `服务: ${provider[1]}` : null
    },
  },
  {
    id: 'register',
    label: '注册账号',
    triggerStart: (l) => l.includes('普通 ChatGPT 网页注册建号') || l.includes('3. 普通'),
    triggerDone: (l) => l.includes('网页注册阶段完成') || l.includes('网页注册后处理完成') || l.includes('切换到登录授权'),
    triggerFail: (l) => l.includes('consumer_chatgpt_registration_failed') || (l.includes('注册') && l.includes('失败') && !l.includes('创建邮箱')),
    extractDetail: (l) => {
      if (l.includes('该邮箱已有账号')) return '已有账号，切换登录'
      if (l.includes('阶段完成')) return '注册成功'
      return null
    },
  },
  {
    id: 'verify',
    label: '邮箱验证',
    triggerStart: (l) => l.includes('label="验证码发送"') || l.includes('OTP send') || l.includes('等待登录验证码'),
    triggerDone: (l) => l.includes('OTP validate status: 200') || l.includes('校验登录验证码') && l.includes('成功'),
    triggerFail: (l) => l.includes('验证码失败') || l.includes('OTP validate failed') || l.includes('等待登录验证码失败'),
    extractDetail: (l) => {
      if (l.includes('OTP send succeeded')) return '验证码已发送'
      if (l.includes('status: 200')) return '验证通过'
      return null
    },
  },
  {
    id: 'phone',
    label: '手机验证',
    triggerStart: (l) => l.includes('add_phone') || l.includes('phone_verification') || l.includes('Acquired phone') || l.includes('SMSBOWER config'),
    triggerDone: (l) => l.includes('Phone verification succeeded') || l.includes('跳过 add-phone'),
    triggerFail: (l) => l.includes('phone verification failed') || l.includes('手机验证失败') || l.includes('SMSBOWER API key missing') || l.includes('All') && l.includes('phone numbers failed'),
    extractDetail: (l) => {
      const m = l.match(/Acquired phone.*?(\+\d+)/)
      return m ? `号码: ${m[1]}` : null
    },
  },
  {
    id: 'payment',
    label: '支付处理',
    triggerStart: (l) => l.includes('[AutoPay] start payment') || l.includes('Plus 支付') || l.includes('payment plan='),
    triggerDone: (l) => l.includes('[AutoPay]') && (l.includes('succeeded') || l.includes('duplicate succeeded payment skipped')),
    triggerFail: (l) => l.includes('[AutoPay]') && (l.includes('payment failed') || l.includes('exception')),
    extractDetail: (l) => {
      // PayPal 子阶段
      if (l.includes('[Browser] 启动 PayPal')) return '🌐 PayPal 浏览器启动'
      if (l.includes('[B-DDC]') && l.includes('检测到')) return '🛡️ DataDome 挑战'
      if (l.includes('[B-DDC]') && l.includes('成功')) return '✅ DataDome 通过'
      if (l.includes('CARD_DATADOME_SLIDER')) return '❌ DataDome 滑块失败'
      if (l.includes('[hCaptcha]') && l.includes('解题成功')) return '✅ hCaptcha 通过'
      if (l.includes('hCaptcha') && l.includes('失败')) return '❌ hCaptcha 失败'
      if (l.includes('[B6]') && l.includes('授权页')) return '📋 PayPal 授权页'
      if (l.includes('[B7]') && l.includes('hermes')) return '🔑 Hermes 授权'
      if (l.includes('[B8]') && l.includes('GraphQL')) return '📡 GraphQL authorize'
      if (l.includes('[B9]') && l.includes('完成')) return '✅ PayPal 回调完成'
      if (l.includes('pm-redirects') && l.includes('未捕获')) return '❌ 回调超时'
      // GoPay API 子阶段
      if (l.includes('GoPay 自动注册启动')) return '📱 GoPay API 注册中'
      if (l.includes('OTP') && l.includes('收到')) return '📨 OTP 收到'
      if (l.includes('OTP') && l.includes('超时')) return '⏰ OTP 超时'
      if (l.includes('[gopay]') && l.includes('redirect')) return '🔗 GoPay 支付中'
      // GoPay Android 子阶段
      if (l.includes('gopay_android provider')) return '📱 GoPay Android 启动'
      if (l.includes('Phase 1') && l.includes('设备就绪')) return '🖥️ 模拟器启动'
      if (l.includes('Phase 2') && l.includes('健康检查')) return '🔍 设备健康检查'
      if (l.includes('Play Services') && l.includes('✅')) return '✅ Play Services 正常'
      if (l.includes('Play Services') && l.includes('❌')) return '❌ 缺 Play Services'
      if (l.includes('Phase 3') && l.includes('启动')) return '📱 App 启动中'
      if (l.includes('Play Integrity') && l.includes('拦截')) return '🚫 Play Integrity 不通过'
      if (l.includes('Phase 4') && l.includes('登录')) return '🔐 App 登录'
      if (l.includes('Phase 5') && l.includes('OTP')) return '📩 等待 OTP'
      if (l.includes('授权页可达')) return '✅ GoPay 授权可达'
      if (l.includes('Phase 7') && l.includes('PIN')) return '🔢 输入 GoPay PIN'
      if (l.includes('PIN 输入完成')) return '✅ PIN 验证通过'
      if (l.includes('PIN 验证失败')) return '❌ PIN 验证失败'
      if (l.includes('Phase 8') && l.includes('支付完成')) return '💰 检测支付完成'
      if (l.includes('支付完成')) return '✅ 支付成功'
      if (l.includes('未检测到支付成功')) return '⚠️ 支付确认超时'
      if (l.includes('[OTP] 通过 SMSBower')) return '📡 SMSBower 获取 OTP'
      if (l.includes('从 SMS 读取 OTP')) return '📱 设备 SMS 读取 OTP'
      if (l.includes('缺少 payment_gopay_phone')) return '❌ 缺手机号配置'
      if (l.includes('缺少 payment_gopay_pin')) return '❌ 缺PIN配置'
      if (l.includes('缺少 smsbower_api_key')) return '❌ 缺OTP配置'
      if (l.includes('实验完成') || l.includes('流程完成')) return '📊 Android 实验完成'
      // Provider 路由
      if (l.includes('provider=paypal_web')) return '🌐 PayPal Web 路线'
      if (l.includes('provider=gopay_api')) return '📡 GoPay API 路线'
      if (l.includes('provider=gopay_android')) return '📱 GoPay Android 路线'
      if (l.includes('provider=manual_link')) return '🔗 手动支付'
      if (l.includes('provider=card')) return '💳 信用卡路线'
      // Checkout
      if (l.includes('启动支付流程')) return '🚀 启动 Checkout'
      // 诊断码
      const dm = l.match(/diagnostic_code=(\S+)/)
      if (dm) return `🔍 诊断: ${dm[1]}`
      return null
    },
  },
  {
    id: 'oauth',
    label: '获取授权令牌',
    triggerStart: (l) => l.includes('OAuth') && (l.includes('授权') || l.includes('登录') || l.includes('callback')) || l.includes('token_exchange'),
    triggerDone: (l) => l.includes('注册流程完成') || l.includes('登录流程完成') || l.includes('[OK] 注册成功'),
    triggerFail: (l) => l.includes('OAuth') && l.includes('失败') || l.includes('token') && l.includes('失败'),
    extractDetail: (l) => {
      const m = l.match(/Account ID:\s*(\S+)/)
      return m ? `ID: ${m[1].slice(0, 12)}...` : null
    },
  },
]

type StepStatus = 'pending' | 'running' | 'done' | 'error'

interface StepState {
  id: string
  label: string
  status: StepStatus
  detail: string
}

function deriveSteps(lines: string[]): StepState[] {
  const states: StepState[] = STEP_DEFS.map((d) => ({
    id: d.id, label: d.label, status: 'pending' as StepStatus, detail: '',
  }))
  for (const line of lines) {
    for (let i = 0; i < STEP_DEFS.length; i++) {
      const def = STEP_DEFS[i]
      const st = states[i]
      if (def.triggerFail(line)) {
        st.status = 'error'
        const d = def.extractDetail?.(line)
        if (d) st.detail = d
      } else if (def.triggerDone(line)) {
        if (st.status !== 'error') st.status = 'done'
        const d = def.extractDetail?.(line)
        if (d) st.detail = d
      } else if (def.triggerStart(line)) {
        if (st.status === 'pending') st.status = 'running'
        const d = def.extractDetail?.(line)
        if (d) st.detail = d
      } else {
        const d = def.extractDetail?.(line)
        if (d) st.detail = d
      }
    }
  }
  return states
}

// ═══════════════════════════════════════════════════════════════════════════

function StepIcon({ status }: { status: StepStatus }) {
  const cls = status === 'running' ? 'step-icon-running' : status === 'done' ? 'step-icon-done' : ''
  const icon = status === 'pending' ? <ClockCircleOutlined style={{ color: '#94a3b8', fontSize: 18 }} />
    : status === 'running' ? <LoadingOutlined style={{ color: '#3b82f6', fontSize: 18 }} spin />
    : status === 'done' ? <CheckCircleOutlined style={{ color: '#22c55e', fontSize: 18 }} />
    : <ExclamationCircleOutlined style={{ color: '#ef4444', fontSize: 18 }} />
  return <span className={cls}>{icon}</span>
}

function workerStateMeta(state?: string) {
  switch (state) {
    case 'success':
      return { color: '#4ade80', bg: 'rgba(34,197,94,.12)', label: '成功' }
    case 'error':
      return { color: '#f87171', bg: 'rgba(239,68,68,.12)', label: '失败' }
    case 'running':
      return { color: '#60a5fa', bg: 'rgba(59,130,246,.12)', label: '进行中' }
    case 'ready':
      return { color: '#fbbf24', bg: 'rgba(251,191,36,.12)', label: '已就绪' }
    case 'skipped':
      return { color: '#fbbf24', bg: 'rgba(251,191,36,.12)', label: '已跳过' }
    case 'stopped':
      return { color: '#cbd5e1', bg: 'rgba(148,163,184,.12)', label: '已停止' }
    default:
      return { color: '#94a3b8', bg: 'rgba(148,163,184,.12)', label: '等待中' }
  }
}

const STEP_LABEL_COLOR: Record<StepStatus, string> = {
  pending: '#64748b',
  running: '#e2e8f0',
  done: '#94a3b8',
  error: '#fca5a5',
}

const pill = (bg: string, fg: string): React.CSSProperties => ({
  display: 'inline-flex', alignItems: 'center', gap: 4,
  padding: '2px 10px', borderRadius: 99,
  background: bg, color: fg,
  fontSize: 12, fontWeight: 600, lineHeight: '20px',
})

// ═══════════════════════════════════════════════════════════════════════════

interface WorkerStateData {
  index: number
  state?: string
  stage?: string
  email?: string
  message?: string
  provider?: string
  proxy?: string
  updated_at?: string
}

const WorkerCard = memo(
  function WorkerCardImpl({ worker }: { worker: WorkerStateData }) {
    const meta = workerStateMeta(worker.state)
    return (
      <div
        style={{
          padding: '12px 14px',
          borderRadius: 10,
          background: meta.bg,
          border: '1px solid rgba(255,255,255,.06)',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, marginBottom: 6 }}>
          <span style={{ color: '#e2e8f0', fontWeight: 700 }}>Worker #{worker.index}</span>
          <span style={{ color: meta.color, fontSize: 12, fontWeight: 700 }}>{meta.label}</span>
        </div>
        <div style={{ color: '#cbd5e1', fontSize: 12, lineHeight: 1.7 }}>
          <div>{String(worker.email || '未分配账号').trim()}</div>
          <div>阶段: {String(worker.stage || 'pending').trim()}</div>
          {worker.provider ? <div>邮箱: {String(worker.provider).trim()}</div> : null}
          {worker.proxy ? <div>代理: {String(worker.proxy).trim()}</div> : null}
          {worker.message ? <div>说明: {String(worker.message).trim()}</div> : null}
        </div>
      </div>
    )
  },
  (a, b) => {
    const x = a.worker
    const y = b.worker
    return (
      x.index === y.index &&
      x.state === y.state &&
      x.stage === y.stage &&
      x.email === y.email &&
      x.message === y.message &&
      x.provider === y.provider &&
      x.proxy === y.proxy
    )
  },
)

interface TaskMeta {
  progress?: string
  total?: number
  started?: number
  completed?: number
  success?: number
  skipped?: number
  errors?: string[]
  status?: string
  worker_states?: Array<{
    index: number
    state?: string
    stage?: string
    email?: string
    message?: string
    provider?: string
    proxy?: string
    updated_at?: string
  }>
}

interface TaskLogPanelProps {
  taskId: string
  taskMeta?: TaskMeta
  onDone?: () => void
}

type TaskTerminalStatus = 'idle' | 'done' | 'failed' | 'stopped'

export function TaskLogPanel({ taskId, taskMeta, onDone }: TaskLogPanelProps) {
  const [lines, setLines] = useState<string[]>([])
  const [error, setError] = useState('')
  const [terminalStatus, setTerminalStatus] = useState<TaskTerminalStatus>('idle')
  const [skipLoading, setSkipLoading] = useState(false)
  const [stopLoading, setStopLoading] = useState(false)
  const [pauseLoading, setPauseLoading] = useState(false)
  const [paused, setPaused] = useState(false)
  const [stopRequested, setStopRequested] = useState(false)
  const [rawLogOpen, setRawLogOpen] = useState(false)
  const onDoneRef = useRef(onDone)
  const nextSinceRef = useRef(0)

  const isFinished = terminalStatus !== 'idle' || stopRequested

  // 步骤状态
  const stepLines = useMemo(
    () => lines.length > STEP_ANALYSIS_LOG_LINES ? lines.slice(-STEP_ANALYSIS_LOG_LINES) : lines,
    [lines],
  )
  const steps = useMemo(() => deriveSteps(stepLines), [stepLines])
  const visibleRawLines = useMemo(
    () => rawLogOpen ? (lines.length > 500 ? lines.slice(-500) : lines) : [],
    [lines, rawLogOpen],
  )
  const activeStepIdx = useMemo(() => {
    const last = steps.map((s, i) => s.status !== 'pending' ? i : -1).filter((i) => i >= 0)
    return last.length > 0 ? last[last.length - 1] : -1
  }, [steps])

  const handleCopyAll = async () => {
    try {
      await navigator.clipboard.writeText(lines.join('\n'))
      message.success('已复制全部原始日志')
    } catch { message.error('复制失败') }
  }

  const handleSkipCurrent = async () => {
    if (isFinished) return
    setSkipLoading(true)
    try {
      const r = await apiFetch(`/tasks/${taskId}/skip-current`, { method: 'POST' }) as {
        control?: { targeted_skip_attempts?: number }
      }
      const n = Number(r.control?.targeted_skip_attempts || 0)
      message.success(n > 1 ? `已跳过 ${n} 个进行中账号` : '已发送跳过请求')
    } catch (e: unknown) { message.error(e instanceof Error ? e.message : '请求失败') }
    finally { setSkipLoading(false) }
  }

  const handleStopTask = async () => {
    if (isFinished) return
    setStopLoading(true)
    try {
      await apiFetch(`/tasks/${taskId}/stop`, { method: 'POST' })
      setStopRequested(true)
      message.success('正在停止任务')
    } catch (e: unknown) { message.error(e instanceof Error ? e.message : '请求失败') }
    finally { setStopLoading(false) }
  }

  const handlePauseToggle = async () => {
    if (isFinished) return
    setPauseLoading(true)
    try {
      const ep = paused ? 'resume' : 'pause'
      const r = await apiFetch(`/tasks/${taskId}/${ep}`, { method: 'POST' }) as { control?: { paused?: boolean } }
      const np = Boolean(r.control?.paused)
      setPaused(np)
      message.success(np ? '已暂停' : '已恢复')
    } catch (e: unknown) { message.error(e instanceof Error ? e.message : '请求失败') }
    finally { setPauseLoading(false) }
  }

  useEffect(() => { onDoneRef.current = onDone }, [onDone])

  // 日志缓冲：攒一批再刷新 React state，避免每行触发重渲染
  const lineBufRef = useRef<string[]>([])
  const flushRafRef = useRef<number | null>(null)
  const trimClientLogBuffer = useCallback(() => {
    if (lineBufRef.current.length > MAX_CLIENT_LOG_LINES) {
      lineBufRef.current = lineBufRef.current.slice(-MAX_CLIENT_LOG_LINES)
    }
  }, [])
  const scheduleFlush = useCallback(() => {
    if (flushRafRef.current !== null) return
    flushRafRef.current = requestAnimationFrame(() => {
      flushRafRef.current = null
      trimClientLogBuffer()
      const snapshot = lineBufRef.current.slice()
      setLines(snapshot)
    })
  }, [trimClientLogBuffer])

  // SSE 日志流（逻辑不变）
  useEffect(() => {
    if (!taskId) return
    const controller = new AbortController()
    let cancelled = false
    const baseRetryMs = 1000, maxRetryMs = 8000
    nextSinceRef.current = 0
    lineBufRef.current = []
    setLines([]); setError(''); setTerminalStatus('idle')
    setStopRequested(false); setPaused(false)

    const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

    const initSnapshot = async (): Promise<boolean> => {
      try {
        const snap = await apiFetch(`/tasks/${taskId}`) as {
          logs?: string[]; status?: TaskTerminalStatus | string
          log_offset?: number; log_total?: number
          control?: { paused?: boolean; stop_requested?: boolean }
          worker_states?: TaskMeta['worker_states']
        }
        if (cancelled) return true
        const sl = Array.isArray(snap.logs) ? snap.logs : []
        lineBufRef.current = sl.slice(-MAX_CLIENT_LOG_LINES)
        setLines(lineBufRef.current.slice())
        nextSinceRef.current = Number(snap.log_total ?? ((snap.log_offset || 0) + sl.length)) || sl.length
        setPaused(Boolean(snap.control?.paused))
        setStopRequested(Boolean(snap.control?.stop_requested))
        if (snap.status === 'done' || snap.status === 'failed' || snap.status === 'stopped') {
          setTerminalStatus(snap.status); onDoneRef.current?.(); return true
        }
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : '获取任务快照失败')
      }
      return false
    }

    const connectStreamOnce = async (): Promise<boolean> => {
      try {
        const token = getToken()
        const headers: Record<string, string> = {}
        if (token) headers.Authorization = `Bearer ${token}`
        const res = await fetch(`${API_BASE}/tasks/${taskId}/logs/stream?since=${nextSinceRef.current}`, {
          headers, signal: controller.signal,
        })
        if (!res.ok) { setError(`日志流连接失败 (${res.status})`); return true }
        if (!res.body) { setError('日志流未返回数据'); return false }
        setError('')
        const reader = res.body.getReader(), decoder = new TextDecoder()
        let buf = ''
        while (!cancelled) {
          const { done, value } = await reader.read()
          if (done) break
          buf += decoder.decode(value, { stream: true })
          const parts = buf.split('\n\n'); buf = parts.pop() || ''
          for (const part of parts) {
            const match = part.match(/^data:\s*(.+)$/m)
            if (!match) continue
            try {
              const p = JSON.parse(match[1]) as {
                line?: string
                index?: number
                reset?: boolean
                log_offset?: number
                log_total?: number
                done?: boolean
                status?: TaskTerminalStatus
              }
              if (p.reset) {
                nextSinceRef.current = Number(p.log_offset || p.log_total || nextSinceRef.current)
                lineBufRef.current = []
                scheduleFlush()
              }
              if (p.line) {
                nextSinceRef.current = Number.isFinite(p.index) ? Number(p.index) + 1 : nextSinceRef.current + 1
                lineBufRef.current.push(p.line)
                scheduleFlush()
              }
              if (p.done) { setTerminalStatus(p.status || 'done'); onDoneRef.current?.(); return true }
            } catch { /* ignore */ }
          }
        }
        return false
      } catch (e: unknown) {
        if (!cancelled && !(e instanceof DOMException && e.name === 'AbortError')) return false
        return true
      }
    }

    const connectStream = async () => {
      if (await initSnapshot() || cancelled) return
      let retry = 0
      while (!cancelled) {
        if (await connectStreamOnce() || cancelled) return
        retry++
        const ms = Math.min(baseRetryMs * (2 ** (retry - 1)), maxRetryMs)
        setError(`日志流中断，${ms / 1000}s 后重试（第 ${retry} 次）`)
        await sleep(ms)
      }
    }
    void connectStream()
    return () => {
      cancelled = true; controller.abort()
      if (flushRafRef.current !== null) { cancelAnimationFrame(flushRafRef.current); flushRafRef.current = null }
    }
  }, [taskId])

  // 统计
  const stats = useMemo(() => {
    const m = taskMeta || {}
    let total = 0, done = 0
    if (m.progress) {
      const p = String(m.progress).split('/')
      if (p.length === 2) { done = parseInt(p[0], 10) || 0; total = parseInt(p[1], 10) || 0 }
    }
    if (Number(m.total || 0) > 0) total = Number(m.total || 0)
    if (m.completed != null) done = Number(m.completed || 0)
    const started = Number(m.started || 0)
    const success = Number(m.success || 0), skipped = Number(m.skipped || 0)
    const failed = Math.max(0, done - success - skipped)
    const pending = Math.max(0, total - done)
    const percent = total > 0 ? Math.round((done / total) * 100) : 0
    return { total, done, started, success, skipped, failed, pending, percent }
  }, [taskMeta])
  const isBatchTask = stats.total > 1
  const workerStates = Array.isArray(taskMeta?.worker_states) ? taskMeta.worker_states : []

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: 10,
      background: 'linear-gradient(135deg, #0f172a 0%, #1e293b 100%)',
      borderRadius: 12, padding: '16px 18px',
    }}>

      {/* ============ 统计条 ============ */}
      {stats.total > 0 && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
          padding: '10px 14px',
          background: 'rgba(255,255,255,.06)',
          border: '1px solid rgba(255,255,255,.08)',
          borderRadius: 10,
          backdropFilter: 'blur(8px)',
        }}>
          <div style={{ flex: 1, minWidth: 140 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
              <span style={{ fontSize: 14, fontWeight: 700, color: '#e2e8f0' }}>
                {stats.done}/{stats.total}
              </span>
              <span style={{ fontSize: 12, color: '#94a3b8', fontWeight: 500 }}>{stats.percent}%</span>
            </div>
            <Progress
              percent={stats.percent} size="small" showInfo={false}
              strokeColor={stats.percent === 100 ? (stats.failed > 0 ? '#f59e0b' : '#22c55e') : '#3b82f6'}
              trailColor="rgba(255,255,255,.1)"
            />
          </div>
          <Space size={6} wrap>
            <span style={pill('rgba(34,197,94,.15)', '#4ade80')}><CheckCircleOutlined /> 成功 {stats.success}</span>
            <span style={pill('rgba(239,68,68,.15)', '#f87171')}><CloseCircleOutlined /> 失败 {stats.failed}</span>
            <span style={pill('rgba(59,130,246,.15)', '#60a5fa')}><ClockCircleOutlined /> 待完成 {stats.pending}</span>
            {stats.skipped > 0 && <span style={pill('rgba(251,191,36,.15)', '#fbbf24')}><ForwardOutlined /> 跳过 {stats.skipped}</span>}
            {stats.done > 0 && (
              <span style={{ fontSize: 12, color: '#94a3b8', fontWeight: 500 }}>
                {((stats.success / stats.done) * 100).toFixed(0)}%
              </span>
            )}
          </Space>
        </div>
      )}

      {/* ============ 控制栏 ============ */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 6 }}>
        <Space size={6}>
          <Button className="btn-gradient" size="small" icon={<FastForwardOutlined />} onClick={handleSkipCurrent}
            loading={skipLoading} disabled={isFinished}
            style={{ background: 'rgba(255,255,255,.08)', borderColor: 'rgba(255,255,255,.15)', color: '#cbd5e1' }}>
            跳过
          </Button>
          <Button className="btn-gradient" size="small" icon={paused ? <PlayCircleOutlined /> : <PauseCircleOutlined />}
            onClick={handlePauseToggle} loading={pauseLoading} disabled={isFinished}
            style={{ background: 'rgba(255,255,255,.08)', borderColor: 'rgba(255,255,255,.15)', color: '#cbd5e1' }}>
            {paused ? '恢复' : '暂停'}
          </Button>
          <Button className="btn-gradient" size="small" icon={<StopOutlined />} onClick={handleStopTask}
            loading={stopLoading} disabled={isFinished}
            style={{ background: 'rgba(239,68,68,.12)', borderColor: 'rgba(239,68,68,.3)', color: '#fca5a5' }}>
            停止
          </Button>
        </Space>
        {paused && (
          <span style={{ fontSize: 12, color: '#fbbf24', fontWeight: 600 }}>
            <PauseCircleOutlined /> 已暂停
          </span>
        )}
      </div>

      {/* ============ 步骤进度 ============ */}
      <div style={{
        background: 'rgba(255,255,255,.04)',
        border: '1px solid rgba(255,255,255,.06)',
        borderRadius: 10,
        padding: '18px 20px',
      }}>
        {error && (
          <div style={{ color: '#f87171', fontSize: 12, marginBottom: 10, padding: '6px 10px', background: 'rgba(239,68,68,.1)', borderRadius: 6 }}>
            {error}
          </div>
        )}
        {isBatchTask ? (
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
            gap: 10,
          }}>
            {[
              { label: '已启动', value: stats.started, color: '#60a5fa', bg: 'rgba(59,130,246,.12)' },
              { label: '已完成', value: stats.done, color: '#e2e8f0', bg: 'rgba(148,163,184,.12)' },
              { label: '成功', value: stats.success, color: '#4ade80', bg: 'rgba(34,197,94,.12)' },
              { label: '失败', value: stats.failed, color: '#f87171', bg: 'rgba(239,68,68,.12)' },
            ].map((item) => (
              <div key={item.label} style={{
                textAlign: 'center',
                padding: '12px 8px',
                borderRadius: 10,
                background: item.bg,
                border: '1px solid rgba(255,255,255,.06)',
              }}>
                <div style={{ fontSize: 20, fontWeight: 800, color: item.color, lineHeight: 1.1 }}>{item.value}</div>
                <div style={{ fontSize: 11, color: '#64748b', marginTop: 5 }}>{item.label}</div>
              </div>
            ))}
            <div style={{
              gridColumn: '1 / -1',
              padding: '10px 12px',
              borderRadius: 8,
              background: 'rgba(255,255,255,.035)',
              color: '#94a3b8',
              fontSize: 12,
              lineHeight: 1.7,
            }}>
              批量任务日志会按账号并发交错显示。下面增加了每个并发项的实时状态，排查细节时再展开原始日志。
            </div>
            {workerStates.length > 0 && (
              <div
                style={{
                  gridColumn: '1 / -1',
                  display: 'grid',
                  gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
                  gap: 10,
                  maxHeight: 480,
                  overflowY: 'auto',
                  padding: 4,
                  scrollbarWidth: 'thin',
                  contain: 'content',
                }}
              >
                {workerStates.map((worker) => (
                  <WorkerCard key={worker.index} worker={worker as WorkerStateData} />
                ))}
              </div>
            )}
          </div>
        ) : steps.map((step, idx) => {
          const isLast = idx === steps.length - 1
          const isActive = idx === activeStepIdx
          return (
            <div key={step.id} className="step-item" style={{ display: 'flex', gap: 14 }}>
              {/* 左侧竖线 + 图标 */}
              <div style={{
                display: 'flex', flexDirection: 'column', alignItems: 'center',
                width: 28, flexShrink: 0,
              }}>
                <div style={{
                  width: 28, height: 28,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  borderRadius: '50%',
                  background: step.status === 'done' ? 'rgba(34,197,94,.12)'
                    : step.status === 'running' ? 'rgba(59,130,246,.12)'
                    : step.status === 'error' ? 'rgba(239,68,68,.12)'
                    : 'rgba(255,255,255,.04)',
                  transition: 'background .3s ease',
                }}>
                  <StepIcon status={step.status} />
                </div>
                {!isLast && (
                  <div style={{
                    width: 2, flex: 1, minHeight: 20,
                    background: step.status === 'done' ? 'rgba(34,197,94,.25)'
                      : step.status === 'error' ? 'rgba(239,68,68,.25)' : 'rgba(255,255,255,.08)',
                    transition: 'background .3s ease',
                  }} />
                )}
              </div>
              {/* 右侧内容 */}
              <div style={{ paddingBottom: isLast ? 0 : 18, flex: 1, paddingTop: 2 }}>
                <div style={{
                  fontSize: 14, fontWeight: isActive ? 600 : 400,
                  color: STEP_LABEL_COLOR[step.status],
                  lineHeight: '24px',
                  transition: 'color .3s ease, font-weight .2s ease',
                }}>
                  {step.label}
                  {step.status === 'running' && (
                    <span style={{
                      display: 'inline-block', marginLeft: 8,
                      fontSize: 11, color: '#60a5fa', fontWeight: 400,
                      animation: 'pulse 1.5s ease-in-out infinite',
                    }}>
                      进行中...
                    </span>
                  )}
                </div>
                {step.detail && (
                  <div style={{
                    fontSize: 12, color: '#64748b', marginTop: 3,
                    fontFamily: 'SFMono-Regular, Menlo, Monaco, Consolas, monospace',
                  }}>
                    {step.detail}
                  </div>
                )}
              </div>
            </div>
          )
        })}

        {/* 初始等待 */}
        {!isBatchTask && activeStepIdx < 0 && !error && lines.length === 0 && (
          <div style={{ textAlign: 'center', padding: '24px 0', color: '#64748b' }}>
            <LoadingOutlined style={{ fontSize: 22, color: '#3b82f6', marginBottom: 10 }} spin />
            <div style={{ fontSize: 13 }}>正在启动注册流程...</div>
          </div>
        )}

        {/* 终态 */}
        {terminalStatus !== 'idle' && (
          <div className="status-banner" style={{
            marginTop: 14, padding: '10px 16px', borderRadius: 8, textAlign: 'center',
            fontSize: 14, fontWeight: 600,
            background: terminalStatus === 'done' ? 'rgba(34,197,94,.12)'
              : terminalStatus === 'failed' ? 'rgba(239,68,68,.12)' : 'rgba(251,191,36,.12)',
            color: terminalStatus === 'done' ? '#4ade80'
              : terminalStatus === 'failed' ? '#f87171' : '#fbbf24',
            border: `1px solid ${terminalStatus === 'done' ? 'rgba(34,197,94,.2)'
              : terminalStatus === 'failed' ? 'rgba(239,68,68,.2)' : 'rgba(251,191,36,.2)'}`,
          }}>
            {terminalStatus === 'done' ? <><CheckCircleOutlined /> 注册完成</>
              : terminalStatus === 'failed' ? <><CloseCircleOutlined /> 任务失败</>
              : <><MinusCircleOutlined /> 任务已停止</>}
          </div>
        )}
      </div>

      {/* ============ 可折叠原始日志（后端分析用）============ */}
      <Collapse
        ghost
        size="small"
        className="dark-collapse"
        style={{ background: 'transparent' }}
        destroyOnHidden
        onChange={(keys) => setRawLogOpen(Array.isArray(keys) ? keys.includes('raw') : keys === 'raw')}
        items={[{
          key: 'raw',
          label: (
            <span style={{ fontSize: 12, color: '#64748b' }}>
              <CodeOutlined /> 原始日志（{lines.length} 行）
            </span>
          ),
          children: (
            <div style={{ position: 'relative' }}>
              <div style={{
                overflowY: 'auto', overflowX: 'hidden',
                background: 'rgba(0,0,0,.3)', borderRadius: 8, padding: '10px 12px',
                fontFamily: 'SFMono-Regular, Menlo, Monaco, Consolas, monospace',
                fontSize: 11.5, lineHeight: 1.6,
                maxHeight: 300,
                color: '#64748b',
                whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                border: '1px solid rgba(255,255,255,.05)',
              }}>
                {visibleRawLines.map((line, i) => (
                  <div key={i}>{line}</div>
                ))}
              </div>
              <Tooltip title="复制全部原始日志">
                <Button size="small" icon={<CopyOutlined />}
                  onClick={handleCopyAll} disabled={lines.length === 0}
                  style={{
                    position: 'absolute', top: 6, right: 6,
                    background: 'rgba(255,255,255,.08)', borderColor: 'rgba(255,255,255,.12)',
                    color: '#94a3b8',
                  }}
                />
              </Tooltip>
            </div>
          ),
        }]}
      />
    </div>
  )
}

export default TaskLogPanel
