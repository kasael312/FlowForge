import { Navigate, createBrowserRouter } from 'react-router-dom'
import App from './App'
import { RedirectIfAuthed, RequireAdmin, RequireAuth, RequireOwner, RequireWorkspace } from './auth/RequireAuth'
import { useWorkspace } from './lib/WorkspaceContext'
import { OnboardingPage } from './pages/OnboardingPage'
import { OverviewPage } from './pages/biz/OverviewPage'
import { InboxPage } from './pages/biz/InboxPage'
import { CalendarPage } from './pages/biz/CalendarPage'
import { CustomersPage } from './pages/biz/CustomersPage'
import { ServicesPage } from './pages/biz/ServicesPage'
import { ConnectionsPage } from './pages/biz/ConnectionsPage'
import { AssistantPage } from './pages/biz/AssistantPage'
import { AutomationsPage } from './pages/biz/AutomationsPage'
import { WorkspacePage } from './pages/settings/WorkspacePage'
import { DashboardPage } from './pages/DashboardPage'
import { FlowsPage } from './pages/FlowsPage'
import { FlowEditorPage } from './pages/FlowEditorPage'
import { HistoryPage } from './pages/HistoryPage'
import { RunDetailPage } from './pages/RunDetailPage'
import { RunPage } from './pages/RunPage'
import { SharedFlowPage } from './pages/SharedFlowPage'
import { RulesPage } from './pages/RulesPage'
import { SkillsPage } from './pages/SkillsPage'
import { TemplatesPage } from './pages/TemplatesPage'
import { ToolsPage } from './pages/ToolsPage'
import { LoginPage } from './pages/auth/LoginPage'
import { RegisterPage } from './pages/auth/RegisterPage'
import { KeysPage } from './pages/settings/KeysPage'
import { ModelsPage } from './pages/settings/ModelsPage'
import { ProfilePage } from './pages/settings/ProfilePage'
import { BillingPage } from './pages/settings/BillingPage'
import { AdminPage } from './pages/AdminPage'

/** Home: the owner's Tổng quan when a workspace exists, the technical dashboard otherwise (admins). */
function Home() {
  const { current } = useWorkspace()
  if (current === undefined) return null
  return current ? <OverviewPage /> : <DashboardPage />
}

export const router = createBrowserRouter([
  { path: '/shared/:token', element: <SharedFlowPage /> },
  {
    element: <RedirectIfAuthed />,
    children: [
      { path: '/login', element: <LoginPage /> },
      { path: '/register', element: <RegisterPage /> },
    ],
  },
  {
    element: <RequireAuth />,
    children: [
      { path: '/onboarding', element: <OnboardingPage /> },
      {
        element: <RequireWorkspace />,
        children: [
          {
            path: '/',
            element: <App />,
            children: [
              { index: true, element: <Home /> },
              { path: 'inbox', element: <InboxPage /> },
              { path: 'calendar', element: <CalendarPage /> },
              { path: 'customers', element: <CustomersPage /> },
              { path: 'services', element: <ServicesPage /> },
              {
                element: <RequireOwner />,
                children: [
                  { path: 'connections', element: <ConnectionsPage /> },
                  { path: 'assistant', element: <AssistantPage /> },
                  { path: 'automations', element: <AutomationsPage /> },
                  { path: 'settings', element: <Navigate to="/settings/profile" replace /> },
                  { path: 'settings/keys', element: <KeysPage /> },
                  { path: 'settings/models', element: <ModelsPage /> },
                  { path: 'settings/workspace', element: <WorkspacePage /> },
                  { path: 'settings/billing', element: <BillingPage /> },
                ],
              },
              {
                element: <RequireAdmin />,
                children: [
                  { path: 'admin', element: <AdminPage /> },
                  { path: 'dashboard', element: <DashboardPage /> },
                  { path: 'flows', element: <FlowsPage /> },
                  { path: 'flows/:id', element: <FlowEditorPage /> },
                  { path: 'flows/:id/run', element: <RunPage /> },
                  { path: 'runs', element: <HistoryPage /> },
                  { path: 'runs/:id', element: <RunDetailPage /> },
                  { path: 'history', element: <Navigate to="/runs" replace /> },
                  { path: 'templates', element: <TemplatesPage /> },
                  { path: 'tools', element: <ToolsPage /> },
                  { path: 'skills', element: <SkillsPage /> },
                  { path: 'rules', element: <RulesPage /> },
                ],
              },
              { path: 'settings/profile', element: <ProfilePage /> },
            ],
          },
        ],
      },
    ],
  },
])
