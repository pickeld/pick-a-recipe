import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@/components/theme-provider'
import { AppShell } from '@/components/app-shell'
import { Toaster } from '@/components/ui/sonner'
import { LoginPage } from '@/pages/login-page'
import { SetupPage } from '@/pages/setup-page'
import { HomePage } from '@/pages/home-page'
import { JobPage } from '@/pages/job-page'
import { TasksPage } from '@/pages/tasks-page'
import { SettingsPage } from '@/pages/settings-page'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: true,
    },
  },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <BrowserRouter>
          <Routes>
            {/* Login is a bare page — Flask handles the actual OIDC redirect */}
            <Route path="/login" element={<LoginPage />} />
            {/* Bare too: no shell, since there is no session to render one for */}
            <Route path="/setup" element={<SetupPage />} />
            <Route element={<AppShell />}>
              <Route path="/" element={<HomePage />} />
              <Route path="/jobs/:jobId" element={<JobPage />} />
              <Route path="/tasks" element={<TasksPage />} />
              <Route path="/settings" element={<SettingsPage />} />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
          <Toaster position="bottom-right" richColors closeButton />
        </BrowserRouter>
      </ThemeProvider>
    </QueryClientProvider>
  )
}
