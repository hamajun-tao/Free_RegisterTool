import { useCallback, useEffect, useMemo, useState } from 'react'
import { Card, Table, Select, Button, Tag, Space, Popconfirm, Typography, Input } from 'antd'
import type { TableColumnsType } from 'antd'
import { ReloadOutlined, DeleteOutlined } from '@ant-design/icons'
import { apiFetch } from '@/lib/utils'
import type { TaskLogItem, TaskLogListResponse, BatchResult } from '@/types/account'
import PageState from '@/components/PageState'
import { PageHeader, PageSection } from '@/components/ui'

const { Text } = Typography

export default function TaskHistory() {
  const [logs, setLogs] = useState<TaskLogItem[]>([])
  const [total, setTotal] = useState(0)
  const [platform, setPlatform] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedRowKeys, setSelectedRowKeys] = useState<number[]>([])
  const [statusFilter, setStatusFilter] = useState('')
  const [keyword, setKeyword] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({ page: '1', page_size: '50' })
      if (platform) params.set('platform', platform)
      const data = (await apiFetch(`/tasks/logs?${params}`)) as TaskLogListResponse
      setLogs(data.items || [])
      setTotal(data.total || 0)
      setSelectedRowKeys((prev) => prev.filter((key) => data.items.some((item) => item.id === key)))
    } catch (e: any) {
      setError(e?.message || e?.detail || String(e))
    } finally {
      setLoading(false)
    }
  }, [platform])

  useEffect(() => {
    load()
  }, [load])

  const handleBatchDelete = async () => {
    if (selectedRowKeys.length === 0) return
    try {
      const result = (await apiFetch('/tasks/logs/batch-delete', {
        method: 'POST',
        body: JSON.stringify({ ids: selectedRowKeys }),
      })) as BatchResult

      void result
      setSelectedRowKeys([])
      await load()
    } catch {
      return
    }
  }

  const filteredLogs = useMemo(() => {
    const kw = keyword.trim().toLowerCase()
    return logs.filter((item) => {
      if (statusFilter && item.status !== statusFilter) return false
      if (!kw) return true
      return [item.email, item.platform, item.status, item.error]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(kw))
    })
  }, [keyword, logs, statusFilter])

  const successCount = logs.filter((item) => item.status === 'success').length
  const failedCount = logs.filter((item) => item.status !== 'success').length

  const columns: TableColumnsType<TaskLogItem> = [
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 190,
      render: (text: string) => (text ? new Date(text).toLocaleString('zh-CN') : '-'),
    },
    {
      title: '平台',
      dataIndex: 'platform',
      key: 'platform',
      width: 110,
      render: (text: string) => <Tag color="blue">{text}</Tag>,
    },
    {
      title: '邮箱',
      dataIndex: 'email',
      key: 'email',
      render: (text: string) => <span className="mono-text">{text}</span>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (status: string) => (
        <Tag color={status === 'success' ? 'success' : 'error'}>{status === 'success' ? '成功' : '失败'}</Tag>
      ),
    },
    {
      title: '错误信息',
      dataIndex: 'error',
      key: 'error',
      render: (text: string) => text || '-',
    },
  ]

  return (
    <div className="page-container">
      <PageHeader
        eyebrow="Logs"
        title="任务历史"
        subtitle="查看批量注册执行记录，筛选平台并清理历史日志。"
        actions={
          <Space wrap>
            <Text type="secondary">{total} 条记录</Text>
            <Tag color="success">成功 {successCount}</Tag>
            <Tag color={failedCount > 0 ? 'error' : 'default'}>失败 {failedCount}</Tag>
            <Button icon={<ReloadOutlined spin={loading} />} onClick={load} loading={loading}>
              刷新
            </Button>
          </Space>
        }
      />

      <PageSection>
        <div className="toolbar-row">
          <div className="toolbar-row__group">
            <Select
              value={platform}
              onChange={(value) => {
                setPlatform(value)
                setSelectedRowKeys([])
              }}
              style={{ width: 160 }}
              options={[
                { value: '', label: '全部平台' },
                { value: 'trae', label: 'Trae' },
                { value: 'cursor', label: 'Cursor' },
                { value: 'chatgpt', label: 'ChatGPT' },
              ]}
            />
            <Select
              value={statusFilter}
              onChange={(value) => {
                setStatusFilter(value)
                setSelectedRowKeys([])
              }}
              style={{ width: 140 }}
              options={[
                { value: '', label: '全部状态' },
                { value: 'success', label: '成功' },
                { value: 'failed', label: '失败' },
                { value: 'error', label: '错误' },
              ]}
            />
            <Input.Search
              allowClear
              placeholder="搜索邮箱 / 错误原因"
              className="history-search"
              onSearch={setKeyword}
              onChange={(event) => {
                if (!event.target.value) setKeyword('')
              }}
            />
          </div>
          <div className="toolbar-row__group">
            {selectedRowKeys.length > 0 ? <Text type="success">已选 {selectedRowKeys.length} 条</Text> : null}
            {selectedRowKeys.length > 0 ? (
              <Popconfirm title={`确认删除选中的 ${selectedRowKeys.length} 条任务历史？`} onConfirm={handleBatchDelete}>
                <Button danger icon={<DeleteOutlined />}>
                  删除选中
                </Button>
              </Popconfirm>
            ) : null}
          </div>
        </div>
      </PageSection>

      <PageSection>
        <Card bordered={false} className="data-table-card" title="历史日志">
          {error ? (
            <PageState type="error" message={error} onRetry={load} />
          ) : (
            <Table
              rowKey="id"
              columns={columns}
              dataSource={filteredLogs}
              loading={loading}
              rowSelection={{
                selectedRowKeys,
                onChange: (keys) => setSelectedRowKeys(keys as number[]),
              }}
              scroll={{ x: 760 }}
              pagination={{ pageSize: 50, total: filteredLogs.length, showSizeChanger: false }}
              locale={{ emptyText: <PageState type="empty" /> }}
            />
          )}
        </Card>
      </PageSection>
    </div>
  )
}
