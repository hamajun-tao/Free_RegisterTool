import type { CSSProperties, ReactNode } from 'react'
import { Space, Tag, Typography } from 'antd'

const { Text, Title } = Typography

interface PageHeaderProps {
  eyebrow?: string
  title: string
  subtitle?: string
  extra?: ReactNode
  actions?: ReactNode
}

export function PageHeader({ eyebrow, title, subtitle, extra, actions }: PageHeaderProps) {
  return (
    <div className="page-header">
      <div className="page-header__main">
        {eyebrow ? <span className="page-header__eyebrow">{eyebrow}</span> : null}
        <div className="page-header__title-row">
          <Title level={2} style={{ margin: 0 }}>
            {title}
          </Title>
          {extra ? <div className="page-header__extra">{extra}</div> : null}
        </div>
        {subtitle ? (
          <Text className="page-header__subtitle" type="secondary">
            {subtitle}
          </Text>
        ) : null}
      </div>
      {actions ? <div className="page-header__actions">{actions}</div> : null}
    </div>
  )
}

interface PageSectionProps {
  children: ReactNode
  compact?: boolean
  style?: CSSProperties
}

export function PageSection({ children, compact = false, style }: PageSectionProps) {
  return (
    <section className={`page-section${compact ? ' page-section--compact' : ''}`} style={style}>
      {children}
    </section>
  )
}

interface StatHeroItem {
  key: string
  label: string
  value: ReactNode
  hint?: string
  tone?: 'cyan' | 'blue' | 'green' | 'amber' | 'red'
}

interface StatHeroProps {
  title: string
  subtitle?: string
  items: StatHeroItem[]
  action?: ReactNode
}

export function StatHero({ title, subtitle, items, action }: StatHeroProps) {
  return (
    <div className="stat-hero">
      <div className="stat-hero__head">
        <div>
          <div className="stat-hero__label">{title}</div>
          {subtitle ? <Text className="stat-hero__subtitle">{subtitle}</Text> : null}
        </div>
        {action ? <div>{action}</div> : null}
      </div>
      <div className="stat-hero__grid">
        {items.map((item) => (
          <div key={item.key} className={`stat-hero__card stat-hero__card--${item.tone || 'cyan'}`}>
            <div className="stat-hero__metric">{item.value}</div>
            <div className="stat-hero__meta">
              <span>{item.label}</span>
              {item.hint ? <Text type="secondary">{item.hint}</Text> : null}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

interface SurfaceTagListProps {
  items: Array<{ key: string; label: ReactNode; color?: string }>
}

export function SurfaceTagList({ items }: SurfaceTagListProps) {
  return (
    <Space wrap size={[8, 8]}>
      {items.map((item) => (
        <Tag key={item.key} color={item.color}>
          {item.label}
        </Tag>
      ))}
    </Space>
  )
}
