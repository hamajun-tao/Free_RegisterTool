import { useEffect, useState } from 'react'
import { Card, Table, Button, Input, Tag, Space, Popconfirm, message, Typography } from 'antd'
import {
  PlusOutlined,
  DeleteOutlined,
  ReloadOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  SwapRightOutlined,
  SwapLeftOutlined,
} from '@ant-design/icons'
import { apiFetch } from '@/lib/utils'
import { PageHeader, PageSection } from '@/components/ui'

const { Text } = Typography

export default function Proxies() {
  const [proxies, setProxies] = useState<any[]>([])
  const [newProxy, setNewProxy] = useState('')
  const [region, setRegion] = useState('')
  const [checking, setChecking] = useState(false)
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const data = await apiFetch('/proxies')
      setProxies(data)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const add = async () => {
    if (!newProxy.trim()) return
    const lines = newProxy
      .trim()
      .split('\n')
      .map((l) => l.trim())
      .filter(Boolean)
    try {
      if (lines.length > 1) {
        await apiFetch('/proxies/bulk', {
          method: 'POST',
          body: JSON.stringify({ proxies: lines, region }),
        })
      } else {
        await apiFetch('/proxies', {
          method: 'POST',
          body: JSON.stringify({ url: lines[0], region }),
        })
      }
      message.success('添加成功')
      setNewProxy('')
      setRegion('')
      load()
    } catch (e: any) {
      message.error(`添加失败: ${e.message}`)
    }
  }

  const del = async (id: number) => {
    try {
      await apiFetch(`/proxies/${id}`, { method: 'DELETE' })
      message.success('删除成功')
      load()
    } catch (e: any) {
      message.error(`删除失败: ${e.message || e}`)
    }
  }

  const toggle = async (id: number) => {
    try {
      await apiFetch(`/proxies/${id}/toggle`, { method: 'PATCH' })
      load()
    } catch (e: any) {
      message.error(`操作失败: ${e.message || e}`)
    }
  }

  const check = async () => {
    setChecking(true)
    try {
      await apiFetch('/proxies/check', { method: 'POST' })
      await load()
    } catch (e: any) {
      message.error(`检测失败: ${e.message || e}`)
    } finally {
      setChecking(false)
    }
  }

  const columns: any[] = [
    {
      title: '代理地址',
      dataIndex: 'url',
      key: 'url',
      render: (text: string) => <span className="mono-text">{text}</span>,
    },
    {
      title: '地区',
      dataIndex: 'region',
      key: 'region',
      width: 120,
      render: (text: string) => text || '-',
    },
    {
      title: '成功 / 失败',
      key: 'stats',
      width: 140,
      render: (_: any, record: any) => (
        <Space>
          <Tag color="success">{record.success_count}</Tag>
          <span>/</span>
          <Tag color="error">{record.fail_count}</Tag>
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      key: 'is_active',
      width: 120,
      render: (active: boolean) => (
        <Tag color={active ? 'success' : 'error'} icon={active ? <CheckCircleOutlined /> : <CloseCircleOutlined />}>
          {active ? '活跃' : '禁用'}
        </Tag>
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 120,
      render: (_: any, record: any) => (
        <Space>
          <Button
            type="text"
            size="small"
            icon={record.is_active ? <SwapLeftOutlined /> : <SwapRightOutlined />}
            onClick={() => toggle(record.id)}
          />
          <Popconfirm title="确认删除？" onConfirm={() => del(record.id)}>
            <Button type="text" size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div className="page-container">
      <PageHeader
        eyebrow="Network"
        title="代理管理"
        subtitle="集中维护注册代理、区域标签和可用性状态。"
        actions={
          <Button icon={<ReloadOutlined spin={checking} />} onClick={check} loading={checking}>
            检测全部代理
          </Button>
        }
      />

      <PageSection>
        <Card bordered={false} title="添加代理">
          <Space direction="vertical" style={{ width: '100%' }} size="middle">
            <Text className="surface-note">支持单条添加和多行批量导入，每行一个代理地址。</Text>
            <Input.TextArea
              value={newProxy}
              onChange={(e) => setNewProxy(e.target.value)}
              placeholder="http://user:pass@host:port"
              rows={4}
              style={{ fontFamily: 'var(--font-mono)' }}
            />
            <Space wrap>
              <Input
                value={region}
                onChange={(e) => setRegion(e.target.value)}
                placeholder="地区标签，如 US / SG"
                style={{ width: 220 }}
              />
              <Button type="primary" icon={<PlusOutlined />} onClick={add}>
                添加代理
              </Button>
            </Space>
          </Space>
        </Card>
      </PageSection>

      <PageSection>
        <Card bordered={false} className="data-table-card" title="代理列表" extra={<Tag color="blue">{proxies.length} Items</Tag>}>
          <Table rowKey="id" columns={columns} dataSource={proxies} loading={loading} pagination={false} />
        </Card>
      </PageSection>
    </div>
  )
}
