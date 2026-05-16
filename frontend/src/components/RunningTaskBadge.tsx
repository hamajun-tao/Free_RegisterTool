import { Badge, Button, Space, Tag, Tooltip, Typography } from 'antd'
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
  const status = task.status || (polling ? 'running' : 'done')
  const isFinished = status === 'done' || status === 'failed' || status === 'stopped'
  const isOnTaskPage = location.pathname === '/register-task'

  if (isOnTaskPage) return null

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
      className="running-task-pill"
      onClick={() => {
        if (!isOnTaskPage) navigate('/register-task')
      }}
    >
      <Badge dot={polling} status={statusColor as 'success' | 'error' | 'warning' | 'processing'}>
        {polling ? (
          <LoadingOutlined className="running-task-pill__icon running-task-pill__icon--running" />
        ) : status === 'done' ? (
          <CheckCircleFilled className="running-task-pill__icon running-task-pill__icon--success" />
        ) : (
          <CloseCircleFilled className="running-task-pill__icon running-task-pill__icon--error" />
        )}
      </Badge>

      <div className="running-task-pill__main">
        <span className="running-task-pill__title">注册任务</span>
        <Tag color={statusColor} className="running-task-pill__tag">
          {statusLabel}
        </Tag>
        <Text type="secondary" className="running-task-pill__meta">
          {completed}/{total || '?'}
        </Text>
        <Tooltip title="成功">
          <span className="running-task-pill__success">成功 {success}</span>
        </Tooltip>
        {errorsCount > 0 && (
          <Tooltip title="失败">
            <span className="running-task-pill__error">失败 {errorsCount}</span>
          </Tooltip>
        )}
      </div>

      <Space size={2} className="running-task-pill__actions">
        {!isFinished && (
          <Tooltip title="关闭任务提示">
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
          <RightOutlined className="running-task-pill__arrow" />
        )}
      </Space>
    </div>
  )
}
