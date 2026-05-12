import { Badge, Button, Progress, Space, Tag, Tooltip, Typography } from 'antd'
import {
  CheckCircleFilled,
  CloseCircleFilled,
  CloseOutlined,
  LoadingOutlined,
  RightOutlined,
} from '@ant-design/icons'
import { useNavigate, useLocation } from 'react-router-dom'
import { useRegisterTask } from '@/contexts/RegisterTaskContext'

const { Text } = Typography

export default function RunningTaskBadge() {
  const { task, polling, clearTask } = useRegisterTask()
  const navigate = useNavigate()
  const location = useLocation()

  if (!task) return null

  const total = Number(task.total ?? 0)
  const completed = Number(task.completed ?? 0)
  const success = Number(task.success ?? 0)
  const errorsCount = Array.isArray(task.errors) ? task.errors.length : 0
  const percent = total > 0 ? Math.min(100, Math.round((completed / total) * 100)) : 0

  const status = task.status || (polling ? 'running' : 'done')
  const isFinished = status === 'done' || status === 'failed' || status === 'stopped'
  const isOnTaskPage = location.pathname === '/register-task'

  const statusColor =
    status === 'done'
      ? 'success'
      : status === 'failed'
        ? 'error'
        : status === 'stopped'
          ? 'warning'
          : 'processing'

  const statusLabel =
    status === 'done'
      ? '已完成'
      : status === 'failed'
        ? '失败'
        : status === 'stopped'
          ? '已停止'
          : '运行中'

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        padding: '8px 14px',
        borderRadius: 12,
        background: 'linear-gradient(135deg, rgba(99,102,241,.10), rgba(34,197,94,.08))',
        border: '1px solid var(--rt-border, rgba(148,163,184,.18))',
        boxShadow: '0 4px 14px rgba(15,23,42,.06)',
        cursor: 'pointer',
        userSelect: 'none',
        minWidth: 320,
      }}
      onClick={() => {
        if (!isOnTaskPage) navigate('/register-task')
      }}
    >
      <Badge dot={polling} status={statusColor as 'success' | 'error' | 'warning' | 'processing'}>
        {polling ? (
          <LoadingOutlined style={{ fontSize: 18, color: '#6366f1' }} />
        ) : status === 'done' ? (
          <CheckCircleFilled style={{ fontSize: 18, color: '#10b981' }} />
        ) : (
          <CloseCircleFilled style={{ fontSize: 18, color: '#ef4444' }} />
        )}
      </Badge>

      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            fontSize: 12,
            fontWeight: 600,
            marginBottom: 4,
          }}
        >
          <span>注册任务</span>
          <Tag color={statusColor} style={{ margin: 0, fontSize: 11 }}>
            {statusLabel}
          </Tag>
          <Text type="secondary" style={{ fontSize: 11 }}>
            {completed}/{total || '?'}
          </Text>
          <Space size={6} style={{ marginLeft: 'auto' }}>
            <Tooltip title="成功">
              <span style={{ color: '#10b981', fontSize: 12, fontWeight: 600 }}>
                ✓ {success}
              </span>
            </Tooltip>
            {errorsCount > 0 && (
              <Tooltip title="失败">
                <span style={{ color: '#ef4444', fontSize: 12, fontWeight: 600 }}>
                  ✕ {errorsCount}
                </span>
              </Tooltip>
            )}
          </Space>
        </div>
        <Progress
          percent={percent}
          showInfo={false}
          size="small"
          strokeColor={
            status === 'failed'
              ? '#ef4444'
              : status === 'done'
                ? '#10b981'
                : { from: '#6366f1', to: '#22c55e' }
          }
          trailColor="rgba(148,163,184,.18)"
          style={{ margin: 0 }}
        />
      </div>

      <Space size={4}>
        {!isFinished && (
          <Tooltip title="关闭任务栏">
            <Button
              type="text"
              size="small"
              icon={<CloseOutlined />}
              onClick={(e) => {
                e.stopPropagation()
                clearTask()
              }}
            />
          </Tooltip>
        )}
        {isFinished ? (
          <Button
            type="text"
            size="small"
            onClick={(e) => {
              e.stopPropagation()
              clearTask()
            }}
          >
            清除
          </Button>
        ) : (
          <RightOutlined style={{ color: 'rgba(148,163,184,.7)', fontSize: 12 }} />
        )}
      </Space>
    </div>
  )
}
