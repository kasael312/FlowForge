import { useEffect, useState, type ComponentType, type SVGProps } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../../auth/AuthContext'
import { useWorkspace } from '../../lib/WorkspaceContext'
import { applyTheme, getStoredTheme, nextTheme, type Theme } from '../../lib/theme'
import {
  IconChevron,
  IconDashboard,
  IconFlows,
  IconKey,
  IconLogout,
  IconModel,
  IconMonitor,
  IconMoon,
  IconRule,
  IconRuns,
  IconSettings,
  IconSkill,
  IconSun,
  IconTemplates,
  IconTool,
  IconUser,
  IconWorkspace,
} from './Icons'

type Icon = ComponentType<SVGProps<SVGSVGElement>>
interface Item {
  to: string
  label: string
  icon: Icon
  end?: boolean
}

/** Owner / staff navigation (Vietnamese): what a gym or spa needs day to day. */
const BUSINESS: Item[] = [
  { to: '/', label: 'Tổng quan', icon: IconDashboard, end: true },
  { to: '/inbox', label: 'Hộp thư', icon: IconRuns },
  { to: '/calendar', label: 'Lịch', icon: IconTemplates },
  { to: '/customers', label: 'Khách hàng', icon: IconUser },
  { to: '/services', label: 'Gói & dịch vụ', icon: IconTemplates },
  { to: '/automations', label: 'Việc tự động', icon: IconFlows },
  { to: '/connections', label: 'Kết nối', icon: IconKey },
]
const STAFF_HIDDEN = new Set(['/automations', '/connections'])
const BUSINESS_SETTINGS: Item[] = [
  { to: '/settings/workspace', label: 'Cơ sở', icon: IconWorkspace },
  { to: '/settings/billing', label: 'Gói & thanh toán', icon: IconKey },
  { to: '/assistant', label: 'Trợ lý AI', icon: IconSkill },
  { to: '/settings/models', label: 'Model AI', icon: IconModel },
  { to: '/settings/keys', label: 'API Keys', icon: IconKey },
  { to: '/settings/profile', label: 'Tài khoản', icon: IconUser },
]
/** Technical FlowForge: only system admins see the canvas and friends. */
const TECH: Item[] = [
  { to: '/admin', label: 'Quản trị', icon: IconWorkspace },
  { to: '/dashboard', label: 'Dashboard', icon: IconDashboard },
  { to: '/flows', label: 'Flows', icon: IconFlows },
  { to: '/runs', label: 'Runs', icon: IconRuns },
  { to: '/templates', label: 'Templates', icon: IconTemplates },
  { to: '/tools', label: 'Tools', icon: IconTool },
  { to: '/skills', label: 'Skills', icon: IconSkill },
  { to: '/rules', label: 'Rules', icon: IconRule },
]

const COLLAPSED_KEY = 'flowforge.sidebar.collapsed'

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSED_KEY) === '1'
  } catch {
    return false
  }
}

interface SidebarProps {
  mobileOpen: boolean
  onMobileClose: () => void
}

/** Left navigation: collapsible on desktop, a drawer on mobile. Technical pages only for admins. */
export function Sidebar({ mobileOpen, onMobileClose }: SidebarProps) {
  const { user, logout } = useAuth()
  const { current: workspace } = useWorkspace()
  const navigate = useNavigate()
  const location = useLocation()
  const [collapsed, setCollapsed] = useState<boolean>(readCollapsed)
  const [theme, setTheme] = useState<Theme>(getStoredTheme)

  useEffect(() => {
    onMobileClose()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- close the drawer on navigation only
  }, [location.pathname])

  const toggleCollapsed = () => {
    const next = !collapsed
    setCollapsed(next)
    try {
      localStorage.setItem(COLLAPSED_KEY, next ? '1' : '0')
    } catch {
      /* private mode */
    }
  }
  const cycleTheme = () => {
    const next = nextTheme(theme)
    setTheme(next)
    applyTheme(next)
  }

  const ThemeIcon = theme === 'light' ? IconSun : theme === 'dark' ? IconMoon : IconMonitor
  const themeLabel = theme === 'light' ? 'Sáng' : theme === 'dark' ? 'Tối' : 'Theo hệ thống'
  const initials = (user?.email ?? '?').slice(0, 2).toUpperCase()
  const isAdmin = Boolean(user?.is_admin)
  const hasWorkspace = Boolean(workspace)
  // Staff only work the day-to-day pages: no automations, connections or settings.
  const isStaff = hasWorkspace && workspace?.role === 'staff' && !isAdmin
  const main = hasWorkspace ? (isStaff ? BUSINESS.filter((i) => !STAFF_HIDDEN.has(i.to)) : BUSINESS) : []
  // Without a workspace (an admin who skipped onboarding) only the technical settings make sense.
  const settings = isStaff ? [] : hasWorkspace ? BUSINESS_SETTINGS : BUSINESS_SETTINGS.filter((s) => s.to === '/settings/profile' || (isAdmin && s.to.startsWith('/settings/')))
  const settingsActive = location.pathname.startsWith('/settings') || location.pathname === '/assistant'

  const linkClass = ({ isActive }: { isActive: boolean }) =>
    `group flex items-center gap-3 rounded-md px-2.5 py-2 text-sm font-medium transition ${
      isActive
        ? 'bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900'
        : 'text-zinc-700 hover:bg-zinc-200 dark:text-zinc-300 dark:hover:bg-zinc-800'
    } ${collapsed ? 'justify-center' : ''}`

  const renderItem = (item: Item) => (
    <NavLink key={item.to} to={item.to} end={item.end} className={linkClass} title={collapsed ? item.label : undefined}>
      <item.icon className="shrink-0" />
      {!collapsed && <span className="truncate">{item.label}</span>}
    </NavLink>
  )
  const section = (title: string, active: boolean, items: Item[]) => (
    <>
      <div className={`pt-3 ${collapsed ? 'text-center' : 'px-2.5'}`}>
        <div className={`flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide ${active ? 'text-zinc-900 dark:text-zinc-100' : 'text-zinc-500'} ${collapsed ? 'justify-center' : ''}`}>
          <IconSettings width={14} height={14} />
          {!collapsed && title}
        </div>
      </div>
      <div className={collapsed ? '' : 'ml-2 border-l border-zinc-200 pl-2 dark:border-zinc-800'}>{items.map(renderItem)}</div>
    </>
  )

  const content = (
    <div className="flex h-full flex-col">
      <div className={`flex h-14 items-center border-b border-zinc-200 px-3 dark:border-zinc-800 ${collapsed ? 'justify-center' : 'justify-between'}`}>
        {!collapsed && <span className="text-base font-bold tracking-tight">FlowForge</span>}
        <button
          type="button"
          onClick={toggleCollapsed}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          aria-expanded={!collapsed}
          className="hidden rounded p-1 text-zinc-500 hover:bg-zinc-200 md:block dark:hover:bg-zinc-800"
        >
          <IconChevron className={`transition-transform ${collapsed ? 'rotate-180' : ''}`} />
        </button>
      </div>

      {workspace && !collapsed && (
        <NavLink to={isStaff ? '/' : '/settings/workspace'} end className="mx-2 mt-2 block truncate rounded-md border border-zinc-200 px-2.5 py-1.5 text-xs dark:border-zinc-800" data-testid="workspace-name" title={workspace.industry_label}>
          <span className="block text-[10px] uppercase tracking-wide text-zinc-500">Cơ sở</span>
          <span className="font-medium">{workspace.name}</span>
        </NavLink>
      )}
      <nav className="flex-1 space-y-1 overflow-y-auto p-2" aria-label="Main">
        {main.map(renderItem)}
        {settings.length > 0 && section('Cài đặt', settingsActive, settings)}
        {isAdmin && section('Kỹ thuật (admin)', TECH.some((t) => location.pathname.startsWith(t.to)), TECH)}
      </nav>

      <div className="border-t border-zinc-200 p-2 dark:border-zinc-800">
        <button
          type="button"
          onClick={cycleTheme}
          className={`mb-1 flex w-full items-center gap-3 rounded-md px-2.5 py-2 text-sm text-zinc-600 hover:bg-zinc-200 dark:text-zinc-400 dark:hover:bg-zinc-800 ${collapsed ? 'justify-center' : ''}`}
          aria-label={`Theme: ${theme}`}
          title={`Giao diện: ${themeLabel} (bấm để đổi)`}
        >
          <ThemeIcon className="shrink-0" />
          {!collapsed && <span>Giao diện: {themeLabel}</span>}
        </button>
        <div className={`flex items-center gap-2 rounded-md px-2 py-1.5 ${collapsed ? 'flex-col' : ''}`}>
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-zinc-900 text-xs font-bold text-white dark:bg-zinc-100 dark:text-zinc-900" aria-hidden>
            {initials}
          </span>
          {!collapsed && (
            <span className="min-w-0 flex-1 truncate text-xs text-zinc-600 dark:text-zinc-400" title={user?.email} data-testid="current-user">
              {user?.email}
            </span>
          )}
          <button
            type="button"
            onClick={() => {
              void logout().then(() => navigate('/login', { replace: true }))
            }}
            className="rounded p-1.5 text-zinc-500 hover:bg-zinc-200 hover:text-rose-600 dark:hover:bg-zinc-800"
            aria-label="Logout"
            title="Đăng xuất"
          >
            <IconLogout />
          </button>
        </div>
      </div>
    </div>
  )

  return (
    <>
      <aside
        data-testid="sidebar"
        data-collapsed={collapsed}
        className={`hidden h-screen shrink-0 border-r border-zinc-200 bg-zinc-50 transition-[width] md:sticky md:top-0 md:block dark:border-zinc-800 dark:bg-zinc-900 ${collapsed ? 'w-16' : 'w-60'}`}
      >
        {content}
      </aside>
      {mobileOpen && (
        <div className="fixed inset-0 z-40 md:hidden" role="dialog" aria-label="Navigation">
          <div className="absolute inset-0 bg-black/40" onClick={onMobileClose} aria-hidden />
          <div data-testid="sidebar-drawer" className="absolute inset-y-0 left-0 w-64 bg-zinc-50 shadow-xl dark:bg-zinc-900">
            {content}
          </div>
        </div>
      )}
    </>
  )
}
