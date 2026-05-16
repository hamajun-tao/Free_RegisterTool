import { Spin, Result, Button } from 'antd'

interface PageStateProps {
  type: 'loading' | 'empty' | 'error'
  message?: string
  onRetry?: () => void
}

export default function PageState({ type, message, onRetry }: PageStateProps) {
  if (type === 'loading') {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        <Spin size="large" />
        {message ? <div style={{ marginTop: 16, color: 'var(--text-secondary)' }}>{message}</div> : null}
      </div>
    )
  }

  if (type === 'error') {
    return (
      <Result
        status="error"
        title="加载失败"
        subTitle={message || '请检查网络连接后重试'}
        extra={
          onRetry && (
            <Button type="primary" onClick={onRetry}>
              重试
            </Button>
          )
        }
      />
    )
  }

  return (
    <Result
      status="info"
      title="暂无数据"
      subTitle={message || '当前没有可显示的内容'}
    />
  )
}
