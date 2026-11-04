import { lazy, Suspense } from 'react'
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

// The kitchen pages load on first visit, so the extraction screens stay light.
const LibraryPage = lazy(() => import('@/pages/library-page').then((m) => ({ default: m.LibraryPage })))
const RecipePage = lazy(() => import('@/pages/recipe-page').then((m) => ({ default: m.RecipePage })))
const CookModePage = lazy(() => import('@/pages/cook-mode-page').then((m) => ({ default: m.CookModePage })))
const PlannerPage = lazy(() => import('@/pages/planner-page').then((m) => ({ default: m.PlannerPage })))
const ShoppingPage = lazy(() => import('@/pages/shopping-page').then((m) => ({ default: m.ShoppingPage })))
const PantryPage = lazy(() => import('@/pages/pantry-page').then((m) => ({ default: m.PantryPage })))
const SousChefPage = lazy(() => import('@/pages/souschef-page').then((m) => ({ default: m.SousChefPage })))
const ImportPage = lazy(() => import('@/pages/import-page').then((m) => ({ default: m.ImportPage })))
const CookLogPage = lazy(() => import('@/pages/cook-log-page').then((m) => ({ default: m.CookLogPage })))
const SharedRecipePage = lazy(() => import('@/pages/shared-recipe-page').then((m) => ({ default: m.SharedRecipePage })))

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
          <Suspense fallback={null}>
          <Routes>
            {/* Login is a bare page — Flask handles the actual OIDC redirect */}
            <Route path="/login" element={<LoginPage />} />
            {/* Bare too: no shell, since there is no session to render one for */}
            <Route path="/setup" element={<SetupPage />} />
            {/* Public share links work without a session */}
            <Route path="/s/:token" element={<SharedRecipePage />} />
            {/* Full screen, so the kitchen display has nothing else on it */}
            <Route path="/library/:recipeId/cook" element={<CookModePage />} />
            <Route element={<AppShell />}>
              <Route path="/" element={<HomePage />} />
              <Route path="/jobs/:jobId" element={<JobPage />} />
              <Route path="/tasks" element={<TasksPage />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="/library" element={<LibraryPage />} />
              <Route path="/library/:recipeId" element={<RecipePage />} />
              <Route path="/planner" element={<PlannerPage />} />
              <Route path="/shopping" element={<ShoppingPage />} />
              <Route path="/pantry" element={<PantryPage />} />
              <Route path="/souschef" element={<SousChefPage />} />
              <Route path="/import" element={<ImportPage />} />
              <Route path="/cook-log" element={<CookLogPage />} />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
          </Suspense>
          <Toaster position="bottom-center" richColors closeButton />
        </BrowserRouter>
      </ThemeProvider>
    </QueryClientProvider>
  )
}
