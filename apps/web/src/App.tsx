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
  AUTH_UNAUTHORIZED_EVENT,
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
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@systutor/shell/ui/card'
import { AuthzProvider, hasPermissionMeta, type PermissionMeta } from './authz'
import { usePluginRegistry } from './plugins'

type NavItem = {
  label: string
  to: string
} & PermissionMeta

const CORE_NAV_ITEMS: NavItem[] = [
  { label: 'Plugins', to: '/plugins', requiredAnyPermissions: ['core.plugin.runtime.read', 'core.plugin.manage'] },
  { label: 'Roles', to: '/roles', requiredAnyPermissions: ['core.roles.manage', 'core.permission.manage'] },
  { label: 'Usuarios', to: '/users', requiredAllPermissions: ['core.users.read'], requiredAnyPermissions: ['core.roles.read', 'core.roles.manage'] },
  { label: 'Branches', to: '/branches', requiredAnyPermissions: ['core.branches.read', 'core.branches.manage'] },
]

type AuthState = {
  user: UserProfile | null
  loading: boolean
  setUser: (user: UserProfile | null) => void
}

const AuthContext = createContext<AuthState>({
  user: null,
  loading: true,
  setUser: () => {},
})

function useAuth() {
  return useContext(AuthContext)
}

function LoginScreen() {
  const navigate = useNavigate()
  const { user, setUser } = useAuth()
  if (user) return <Navigate to="/" replace />
  return (
    <Login
      title="Spanel"
      onLogin={(loggedUser) => {
        setUser(loggedUser)
        navigate('/', { replace: true })
      }}
    />
  )
}

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()
  const location = useLocation()
  if (loading) return null
  if (!user) return <Navigate to="/login" state={{ from: location }} replace />
  return children
}

function NoModulesState() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Sin módulos habilitados</CardTitle>
        <CardDescription>Este usuario no tiene acceso a ningún módulo visible.</CardDescription>
      </CardHeader>
    </Card>
  )
}

function ForbiddenState({ defaultPath }: { defaultPath: string | null }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>No autorizado</CardTitle>
        <CardDescription>No tenés permiso para acceder a este módulo.</CardDescription>
      </CardHeader>
      <CardContent>
        {defaultPath ? (
          <Link to={defaultPath} className="text-sm text-primary hover:underline">
            Ir al primer destino permitido
          </Link>
        ) : (
          <p className="text-sm text-muted-foreground">Sin destinos permitidos para este usuario.</p>
        )}
      </CardContent>
    </Card>
  )
}

function Layout({ coreNavigation, pluginNavigation }: { coreNavigation: NavItem[], pluginNavigation: NavItem[] }) {
  const { user, setUser } = useAuth()
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
              {coreNavigation.map((item) => (
                <Link key={item.to} to={item.to}>
                  {item.label}
                </Link>
              ))}
              {coreNavigation.length > 0 && pluginNavigation.length > 0 && (
                <span className="h-4 w-px bg-border" aria-hidden />
              )}
              {pluginNavigation.map((item) => (
                <Link key={item.to} to={item.to}>
                  {item.label}
                </Link>
              ))}
            </nav>
          </div>
          <div className="flex items-center gap-3">
            <Badge>{user.tenant_name}</Badge>
            <span className="text-sm text-muted-foreground">{user.email}</span>
            <LogoutButton
              onLogout={() => {
                setUser(null)
                navigate('/login', { replace: true })
              }}
            />
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
  const [auth, setAuth] = useState<AuthState>({
    user: null,
    loading: true,
    setUser: (user) => setAuth((prev) => ({ ...prev, user })),
  })
  const userPermissions = auth.user?.permissions ?? []
  const pluginRegistry = usePluginRegistry(userPermissions)
  const coreNavigation = auth.user
    ? CORE_NAV_ITEMS.filter((item) => hasPermissionMeta(userPermissions, item))
    : []
  const defaultPath = [...coreNavigation, ...pluginRegistry.navigation][0]?.to ?? null

  const renderProtected = (meta: PermissionMeta, element: React.ReactNode) => {
    if (auth.loading) return null
    if (!auth.user) return <Navigate to="/login" replace />
    if (!hasPermissionMeta(userPermissions, meta)) return <ForbiddenState defaultPath={defaultPath} />
    return element
  }

  useEffect(() => {
    initAuth()
    if (!getToken()) {
      setAuth((prev) => ({ ...prev, loading: false }))
      return
    }
    apiRequest<UserProfile>('/api/v1/auth/me')
      .then((user) => setAuth((prev) => ({ ...prev, user, loading: false })))
      .catch(() => {
        clearToken()
        setAuth((prev) => ({ ...prev, user: null, loading: false }))
      })
  }, [])

  useEffect(() => {
    const handleUnauthorized = () => {
      clearToken()
      setAuth((prev) => ({ ...prev, user: null, loading: false }))
    }

    window.addEventListener(AUTH_UNAUTHORIZED_EVENT, handleUnauthorized)
    return () => {
      window.removeEventListener(AUTH_UNAUTHORIZED_EVENT, handleUnauthorized)
    }
  }, [])

  return (
    <AuthContext.Provider value={auth}>
      <AuthzProvider user={auth.user}>
        <Router>
          <Routes>
            <Route path="/login" element={<LoginScreen />} />
            <Route
              element={
                <RequireAuth>
                  <Layout coreNavigation={coreNavigation} pluginNavigation={pluginRegistry.navigation} />
                </RequireAuth>
              }
            >
              <Route path="/" element={defaultPath ? <Navigate to={defaultPath} replace /> : <NoModulesState />} />
              <Route path="/plugins" element={renderProtected(CORE_NAV_ITEMS[0], <PluginsView />)} />
              <Route path="/roles" element={renderProtected(CORE_NAV_ITEMS[1], <RolesView />)} />
              <Route path="/users" element={renderProtected(CORE_NAV_ITEMS[2], <UsersView />)} />
              <Route path="/branches" element={renderProtected(CORE_NAV_ITEMS[3], <BranchesView />)} />
              {pluginRegistry.routes.map((route) => (
                <Route key={route.path} path={route.path} element={route.element} />
              ))}
              <Route path="*" element={<ForbiddenState defaultPath={defaultPath} />} />
            </Route>
          </Routes>
        </Router>
      </AuthzProvider>
    </AuthContext.Provider>
  )
}

export default App
