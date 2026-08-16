import { createContext, useContext, useEffect, useState } from 'react'
import {
  Link,
  Navigate,
  Outlet,
  Route,
  BrowserRouter as Router,
  Routes,
  useLocation,
  useNavigate,
} from 'react-router-dom'
import {
  BranchesView,
  Login,
  LogoutButton,
  PluginsView,
  RolesView,
  UsersView,
  apiRequest,
  clearToken,
  getToken,
  initAuth,
  type UserProfile,
} from '@systutor/shell'
import { Badge } from '@systutor/shell/ui/badge'

type AuthState = {
  user: UserProfile | null
  loading: boolean
}

const AuthContext = createContext<AuthState>({ user: null, loading: true })

function useAuth() {
  return useContext(AuthContext)
}

function LoginScreen() {
  const navigate = useNavigate()
  const { user } = useAuth()
  if (user) return <Navigate to="/plugins" replace />
  return <Login title="Spanel" onLogin={() => navigate('/plugins', { replace: true })} />
}

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()
  const location = useLocation()
  if (loading) return null
  if (!user) return <Navigate to="/login" state={{ from: location }} replace />
  return children
}

function Layout() {
  const { user } = useAuth()
  const navigate = useNavigate()
  if (!user) return null

  return (
    <div className="min-h-screen">
      <header className="border-b border-border bg-card">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-6 py-3">
          <div className="flex items-center gap-6">
            <Link to="/" className="text-sm font-semibold">
              Spanel
            </Link>
            <nav className="flex items-center gap-4 text-sm text-muted-foreground">
              <Link to="/plugins">Plugins</Link>
              <Link to="/roles">Roles</Link>
              <Link to="/users">Usuarios</Link>
              <Link to="/branches">Branches</Link>
            </nav>
          </div>
          <div className="flex items-center gap-3">
            <Badge>{user.tenant_name}</Badge>
            <span className="text-sm text-muted-foreground">{user.email}</span>
            <LogoutButton onLogout={() => navigate('/login', { replace: true })} />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-6 py-8">
        <Outlet />
      </main>
    </div>
  )
}

function App() {
  const [auth, setAuth] = useState<AuthState>({ user: null, loading: true })

  useEffect(() => {
    initAuth()
    if (!getToken()) {
      setAuth({ user: null, loading: false })
      return
    }
    apiRequest<UserProfile>('/api/v1/auth/me')
      .then((user) => setAuth({ user, loading: false }))
      .catch(() => {
        clearToken()
        setAuth({ user: null, loading: false })
      })
  }, [])

  return (
    <AuthContext.Provider value={auth}>
      <Router>
        <Routes>
          <Route path="/login" element={<LoginScreen />} />
          <Route
            element={
              <RequireAuth>
                <Layout />
              </RequireAuth>
            }
          >
            <Route path="/" element={<Navigate to="/plugins" replace />} />
            <Route path="/plugins" element={<PluginsView />} />
            <Route path="/roles" element={<RolesView />} />
            <Route path="/users" element={<UsersView />} />
            <Route path="/branches" element={<BranchesView />} />
          </Route>
        </Routes>
      </Router>
    </AuthContext.Provider>
  )
}

export default App
