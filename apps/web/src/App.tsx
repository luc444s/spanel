import { useEffect, useState } from 'react'
import {
  Login,
  LogoutButton,
  apiRequest,
  clearToken,
  getToken,
  initAuth,
  type UserProfile,
} from '@systutor/shell'
import { Badge } from '@systutor/shell/ui/badge'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@systutor/shell/ui/card'

function App() {
  const [user, setUser] = useState<UserProfile | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    initAuth()
    if (!getToken()) {
      setLoading(false)
      return
    }
    apiRequest<UserProfile>('/api/v1/auth/me')
      .then(setUser)
      .catch(() => clearToken())
      .finally(() => setLoading(false))
  }, [])

  if (loading) return null

  if (!user) {
    return <Login title="Spanel" onLogin={setUser} />
  }

  return (
    <main className="min-h-screen flex items-center justify-center p-6">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>Spanel</CardTitle>
          <CardDescription>Sesión activa</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-2">
            <Badge>{user.email}</Badge>
          </div>
          <p className="text-sm text-muted-foreground">
            {user.full_name} · {user.tenant_name}
            {user.branch_name ? ` · ${user.branch_name}` : ''}
          </p>
          <div className="flex justify-end">
            <LogoutButton onLogout={() => setUser(null)} />
          </div>
        </CardContent>
      </Card>
    </main>
  )
}

export default App
