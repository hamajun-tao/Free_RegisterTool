import { Suspense, lazy, useEffect, useState } from 'react'
import { BrowserRouter, Routes, Route, useLocation, useNavigate } from 'react-router-dom'
import { ConfigProvider, Layout, Menu, Button, Space, Spin, Typography } from 'antd'
import {
  DashboardOutlined,
  UserOutlined,
  GlobalOutlined,
  HistoryOutlined,
  SettingOutlined,
  ClockCircleOutlined,
  SunOutlined,
  MoonOutlined,
  ControlOutlined,
} from '@ant-design/icons'
import zhCN from 'antd/locale/zh_CN'
import { apiFetch, getToken } from '@/lib/utils'
import { darkTheme, lightTheme } from './theme'
import { RegisterTaskProvider } from '@/contexts/RegisterTaskContext'

const Dashboard = lazy(() => import('@/pages/Dashboard'))
const Accounts = lazy(() => import('@/pages/Accounts'))
const Register = lazy(() => import('@/pages/Register'))
const RegisterTaskPage = lazy(() => import('@/pages/RegisterTaskPage'))
const ScheduledTasks = lazy(() => import('@/pages/ScheduledTasks'))
const Proxies = lazy(() => import('@/pages/Proxies'))
const Settings = lazy(() => import('@/pages/Settings'))
const TaskHistory = lazy(() => import('@/pages/TaskHistory'))
const Login = lazy(() => import('@/pages/Login'))

const { Sider, Content, Header } = Layout
const { Text } = Typography

function RouteFallback() {
  return (
    <div className="route-fallback">
      <Spin size="large" />
      <Text type="secondary">Loading workspace…</Text>
    </div>
  )
}

function AppContent() {
  const [themeMode, setThemeMode] = useState<'dark' | 'light'>(() =>
    (localStorage.getItem('theme') as 'dark' | 'light') || 'light'
  )
  const [collapsed, setCollapsed] = useState(false)
  const [platforms, setPlatforms] = useState<{ key: string; label: string }[]>([])
  const location = useLocation()
  const navigate = useNavigate()

  useEffect(() => {
    document.documentElement.classList.toggle('light', themeMode === 'light')
    localStorage.setItem('theme', themeMode)
  }, [themeMode])

  useEffect(() => {
    apiFetch('/platforms')
      .then((d) =>
        setPlatforms(
          (d || [])
            .filter((p: { name: string }) => p.name !== 'tavily')
            .map((p: { name: string; display_name: string }) => ({
              key: p.name,
              label: p.display_name,
            }))
        )
      )
      .catch(() => setPlatforms([]))
  }, [])

  useEffect(() => {
    fetch('/api/auth/status')
      .then((r) => r.json())
      .then((data) => {
        const hasPassword = !!data?.has_password
        const hasToken = !!getToken()
        if (hasPassword && !hasToken && location.pathname !== '/login') {
          window.location.href = '/login'
        }
        if (!hasPassword && location.pathname === '/login') {
          navigate('/settings', { replace: true })
        }
      })
      .catch(() => undefined)
  }, [location.pathname, navigate])

  const isLight = themeMode === 'light'
  const currentTheme = isLight ? lightTheme : darkTheme

  const getSelectedKey = () => {
    const path = location.pathname
    if (path === '/') return ['/']
    if (path.startsWith('/accounts')) return [path]
    if (path === '/history') return ['/history']
    if (path === '/proxies') return ['/proxies']
    if (path === '/settings') return ['/settings']
    if (path === '/scheduled') return ['/scheduled']
    if (path === '/register-task') return ['/register-task']
    return ['/']
  }

  if (location.pathname === '/login') {
    return (
      <Suspense fallback={<RouteFallback />}>
        <Login />
      </Suspense>
    )
  }

  const menuItems = [
    {
      key: '/',
      icon: <DashboardOutlined />,
      label: '仪表盘',
    },
    {
      key: '/accounts',
      icon: <UserOutlined />,
      label: '平台账号',
      children: platforms.map((p) => ({
        key: `/accounts/${p.key}`,
        label: p.label,
      })),
    },
    {
      key: '/register-task',
      icon: <ControlOutlined />,
      label: '注册任务',
    },
    {
      key: '/history',
      icon: <HistoryOutlined />,
      label: '任务历史',
    },
    {
      key: '/scheduled',
      icon: <ClockCircleOutlined />,
      label: '定时任务',
    },
    {
      key: '/proxies',
      icon: <GlobalOutlined />,
      label: '代理管理',
    },
    {
      key: '/settings',
      icon: <SettingOutlined />,
      label: '全局配置',
    },
  ]

  return (
    <ConfigProvider theme={currentTheme} locale={zhCN}>
      <Layout className="app-shell" style={{ minHeight: '100vh' }}>
        <Sider
          collapsible
          collapsed={collapsed}
          onCollapse={setCollapsed}
          width={248}
          breakpoint="lg"
          style={{
            borderRight: `1px solid ${currentTheme.token?.colorBorder}`,
          }}
        >
          <div className="app-sidebar-brand">
            <Space align="center" size={12}>
              <div className="app-sidebar-brand__mark">
                <DashboardOutlined style={{ fontSize: 18 }} />
              </div>
              {!collapsed ? (
                <div className="app-sidebar-brand__meta">
                  <span className="app-sidebar-brand__title">Account Manager</span>
                  <span className="app-sidebar-brand__subtitle">Fast registration workspace</span>
                </div>
              ) : null}
            </Space>
          </div>

          <Menu
            mode="inline"
            selectedKeys={getSelectedKey()}
            defaultOpenKeys={['/accounts']}
            items={menuItems}
            onClick={({ key }) => navigate(key)}
            style={{ background: 'transparent' }}
          />

          <div
            style={{
              position: 'absolute',
              bottom: 16,
              left: 0,
              right: 0,
              padding: '0 16px',
            }}
          >
            <Button
              block
              icon={isLight ? <MoonOutlined /> : <SunOutlined />}
              onClick={() => setThemeMode(isLight ? 'dark' : 'light')}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: collapsed ? 'center' : 'space-between',
                height: 44,
              }}
            >
              {!collapsed && (isLight ? '切换深色' : '切换亮色')}
            </Button>
          </div>
        </Sider>

        <Layout className="app-content">
          <Header
            className="app-topbar"
            style={{
              position: 'sticky',
              top: 0,
              zIndex: 20,
              height: 'var(--header-height)',
              padding: '0 clamp(16px, 2vw, 28px)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              borderBottom: `1px solid ${currentTheme.token?.colorBorder}`,
            }}
          >
            <div>
              <Text style={{ color: currentTheme.token?.colorTextSecondary, fontSize: 13 }}>
                Auto registration workspace
              </Text>
            </div>
          </Header>

          <Content
            style={{
              padding: '24px clamp(16px, 2vw, 28px)',
              overflow: 'auto',
              minHeight: `calc(100vh - var(--header-height))`,
            }}
          >
            <Suspense fallback={<RouteFallback />}>
              <div key={location.pathname} className="route-stage">
                <Routes>
                  <Route path="/" element={<Dashboard />} />
                  <Route path="/accounts" element={<Accounts />} />
                  <Route path="/accounts/:platform" element={<Accounts />} />
                  <Route path="/register" element={<Register />} />
                  <Route path="/register-task" element={<RegisterTaskPage />} />
                  <Route path="/scheduled" element={<ScheduledTasks />} />
                  <Route path="/history" element={<TaskHistory />} />
                  <Route path="/proxies" element={<Proxies />} />
                  <Route path="/settings" element={<Settings />} />
                  <Route path="/login" element={<Login />} />
                </Routes>
              </div>
            </Suspense>
          </Content>
        </Layout>
      </Layout>
    </ConfigProvider>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <RegisterTaskProvider>
        <AppContent />
      </RegisterTaskProvider>
    </BrowserRouter>
  )
}
