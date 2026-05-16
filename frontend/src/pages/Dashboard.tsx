import { useEffect, useState } from 'react'
import { Card, Row, Col, Progress, Tag, Button, Spin, Statistic } from 'antd'
import {
  UserOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { apiFetch } from '@/lib/utils'
import PageState from '@/components/PageState'
import { PageHeader, PageSection } from '@/components/ui'

const PLATFORM_COLORS: Record<string, string> = {
  trae: '#38bdf8',
  cursor: '#22c55e',
  chatgpt: '#22d3ee',
  grok: '#f59e0b',
  kiro: '#a78bfa',
}

const STATUS_COLORS: Record<string, string> = {
  registered: 'default',
  trial: 'processing',
  subscribed: 'success',
  expired: 'warning',
  invalid: 'error',
}

export default function Dashboard() {
  const [stats, setStats] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await apiFetch('/accounts/stats')
      setStats(data)
    } catch (e: any) {
      setError(e?.message || e?.detail || String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const statCards = [
    {
      title: '总账号数',
      value: stats?.total ?? 0,
      icon: <UserOutlined style={{ fontSize: 20 }} />,
      color: '#38bdf8',
    },
    {
      title: '试用中',
      value: stats?.by_status?.trial ?? 0,
      icon: <ClockCircleOutlined style={{ fontSize: 20 }} />,
      color: '#f59e0b',
    },
    {
      title: '已订阅',
      value: stats?.by_status?.subscribed ?? 0,
      icon: <CheckCircleOutlined style={{ fontSize: 20 }} />,
      color: '#34d399',
    },
    {
      title: '失效账号',
      value: (stats?.by_status?.expired ?? 0) + (stats?.by_status?.invalid ?? 0),
      icon: <CloseCircleOutlined style={{ fontSize: 20 }} />,
      color: '#fb7185',
    },
  ]

  return (
    <div className="page-container">
      <PageHeader
        eyebrow="Overview"
        title="仪表盘"
        subtitle="统一查看账号规模、平台分布和状态健康度。"
        actions={
          <Button icon={<ReloadOutlined spin={loading} />} onClick={load} loading={loading}>
            刷新数据
          </Button>
        }
      />

      <Row gutter={[16, 16]}>
        {statCards.map(({ title, value, icon, color }) => (
          <Col xs={24} sm={12} lg={6} key={title}>
            <div className="metric-card">
              <div className="metric-card__header">
                <div className="metric-card__label">{title}</div>
                <div className="metric-card__icon" style={{ color, background: `${color}1a` }}>
                  {icon}
                </div>
              </div>
              <div className="metric-card__value">{value}</div>
            </div>
          </Col>
        ))}
      </Row>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={14}>
          <PageSection>
            <Card
              bordered={false}
              title="平台分布"
              extra={<Tag color="blue">{stats?.total ?? 0} Accounts</Tag>}
            >
              {error ? (
                <PageState type="error" message={error} onRetry={load} />
              ) : loading ? (
                <div style={{ textAlign: 'center', padding: 40 }}>
                  <Spin />
                </div>
              ) : stats ? (
                Object.entries(stats.by_platform || {}).map(([platform, count]: any) => (
                  <div key={platform} style={{ marginBottom: 18 }}>
                    <div
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        marginBottom: 8,
                      }}
                    >
                      <Tag color={PLATFORM_COLORS[platform] || 'default'}>{platform}</Tag>
                      <span>{count}</span>
                    </div>
                    <Progress
                      percent={stats.total ? Math.round((count / stats.total) * 100) : 0}
                      strokeColor={PLATFORM_COLORS[platform] || '#38bdf8'}
                      trailColor="rgba(125, 211, 252, 0.08)"
                      showInfo={false}
                    />
                  </div>
                ))
              ) : (
                <PageState type="empty" />
              )}
            </Card>
          </PageSection>
        </Col>

        <Col xs={24} lg={10}>
          <PageSection>
            <Card bordered={false} title="状态分布">
              {error ? (
                <PageState type="error" message={error} onRetry={load} />
              ) : loading ? (
                <div style={{ textAlign: 'center', padding: 40 }}>
                  <Spin />
                </div>
              ) : stats ? (
                Object.entries(stats.by_status || {}).map(([status, count]: any) => (
                  <div
                    key={status}
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      padding: '12px 0',
                      borderBottom: '1px solid rgba(125, 211, 252, 0.08)',
                    }}
                  >
                    <Tag color={STATUS_COLORS[status] || 'default'}>{status}</Tag>
                    <Statistic value={count} valueStyle={{ fontSize: 18 }} />
                  </div>
                ))
              ) : (
                <PageState type="empty" />
              )}
            </Card>
          </PageSection>
        </Col>
      </Row>
    </div>
  )
}
