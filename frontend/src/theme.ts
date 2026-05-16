import { theme } from 'antd'

const commonComponents = {
  Button: {
    borderRadius: 14,
    controlHeight: 40,
    paddingInline: 16,
    fontWeight: 600,
  },
  Card: {
    borderRadiusLG: 22,
    headerFontSize: 16,
    headerFontSizeSM: 15,
    bodyPadding: 20,
  },
  Input: {
    borderRadius: 14,
    controlHeight: 42,
  },
  InputNumber: {
    borderRadius: 14,
    controlHeight: 42,
  },
  Select: {
    borderRadius: 14,
    controlHeight: 42,
  },
  Table: {
    headerBorderRadius: 16,
  },
  Modal: {
    borderRadiusLG: 24,
  },
  Tabs: {
    horizontalItemGutter: 24,
    cardGutter: 12,
  },
  Tag: {
    borderRadiusSM: 999,
  },
  Menu: {
    itemBorderRadius: 12,
    subMenuItemBorderRadius: 12,
    itemMarginInline: 10,
    itemMarginBlock: 8,
  },
}

const darkTheme = {
  token: {
    colorPrimary: '#38bdf8',
    colorSuccess: '#34d399',
    colorWarning: '#fbbf24',
    colorError: '#fb7185',
    colorInfo: '#22d3ee',
    colorLink: '#38bdf8',
    colorTextBase: '#e6f4ff',
    colorText: '#e6f4ff',
    colorTextSecondary: '#8ea7bc',
    colorTextTertiary: '#6c859a',
    colorBgBase: '#08121b',
    colorBgLayout: '#08121b',
    colorBgContainer: 'rgba(14, 27, 40, 0.9)',
    colorBgElevated: 'rgba(15, 31, 46, 0.98)',
    colorFillSecondary: 'rgba(56, 189, 248, 0.07)',
    colorFillTertiary: 'rgba(56, 189, 248, 0.05)',
    colorFillAlter: 'rgba(255, 255, 255, 0.025)',
    colorBorder: 'rgba(125, 211, 252, 0.14)',
    colorBorderSecondary: 'rgba(125, 211, 252, 0.08)',
    borderRadius: 14,
    borderRadiusLG: 22,
    fontSize: 14,
    fontFamily: "'Segoe UI', 'PingFang SC', 'Microsoft YaHei', 'Noto Sans SC', system-ui, sans-serif",
    boxShadowSecondary: '0 10px 24px rgba(3, 10, 18, 0.14)',
  },
  components: {
    ...commonComponents,
    Layout: {
      bodyBg: 'transparent',
      siderBg: 'rgba(14, 27, 40, 0.94)',
      triggerBg: 'rgba(15, 31, 46, 0.98)',
      triggerColor: '#c8e7f8',
      headerBg: 'rgba(15, 31, 46, 0.92)',
    },
    Menu: {
      ...commonComponents.Menu,
      itemBg: 'transparent',
      subMenuItemBg: 'transparent',
      itemColor: '#93acc0',
      itemHoverColor: '#e6f4ff',
      itemSelectedColor: '#e6f4ff',
      darkItemBg: 'transparent',
      darkItemColor: '#93acc0',
      darkItemSelectedBg: 'rgba(56, 189, 248, 0.08)',
      darkItemSelectedColor: '#e6f4ff',
      darkSubMenuItemBg: 'transparent',
    },
    Tabs: {
      ...commonComponents.Tabs,
      itemSelectedColor: '#38bdf8',
      inkBarColor: '#22d3ee',
    },
    Table: {
      ...commonComponents.Table,
      borderColor: 'rgba(125, 211, 252, 0.08)',
      rowHoverBg: 'rgba(56, 189, 248, 0.05)',
    },
  },
  algorithm: theme.darkAlgorithm,
}

const lightTheme = {
  token: {
    colorPrimary: '#0891b2',
    colorSuccess: '#10b981',
    colorWarning: '#f59e0b',
    colorError: '#f43f5e',
    colorInfo: '#06b6d4',
    colorLink: '#0891b2',
    colorTextBase: '#0d2235',
    colorText: '#0d2235',
    colorTextSecondary: '#587184',
    colorTextTertiary: '#7790a3',
    colorBgBase: '#f3f8fc',
    colorBgLayout: '#f3f8fc',
    colorBgContainer: 'rgba(255, 255, 255, 0.94)',
    colorBgElevated: 'rgba(255, 255, 255, 1)',
    colorFillSecondary: 'rgba(8, 145, 178, 0.06)',
    colorFillTertiary: 'rgba(8, 145, 178, 0.04)',
    colorFillAlter: 'rgba(8, 145, 178, 0.025)',
    colorBorder: 'rgba(14, 116, 144, 0.1)',
    colorBorderSecondary: 'rgba(14, 116, 144, 0.06)',
    borderRadius: 14,
    borderRadiusLG: 22,
    fontSize: 14,
    fontFamily: "'Segoe UI', 'PingFang SC', 'Microsoft YaHei', 'Noto Sans SC', system-ui, sans-serif",
    boxShadowSecondary: '0 8px 22px rgba(15, 23, 42, 0.06)',
  },
  components: {
    ...commonComponents,
    Layout: {
      bodyBg: 'transparent',
      siderBg: 'rgba(255, 255, 255, 0.96)',
      triggerBg: 'rgba(255, 255, 255, 1)',
      triggerColor: '#0d2235',
      headerBg: 'rgba(255, 255, 255, 0.92)',
    },
    Menu: {
      ...commonComponents.Menu,
      itemBg: 'transparent',
      subMenuItemBg: 'transparent',
      itemColor: '#587184',
      itemHoverColor: '#0d2235',
      itemSelectedColor: '#0d2235',
      itemSelectedBg: 'rgba(8, 145, 178, 0.08)',
    },
    Tabs: {
      ...commonComponents.Tabs,
      itemSelectedColor: '#0891b2',
      inkBarColor: '#06b6d4',
    },
    Table: {
      ...commonComponents.Table,
      borderColor: 'rgba(14, 116, 144, 0.08)',
      rowHoverBg: 'rgba(8, 145, 178, 0.04)',
    },
  },
  algorithm: theme.defaultAlgorithm,
}

export { darkTheme, lightTheme }
