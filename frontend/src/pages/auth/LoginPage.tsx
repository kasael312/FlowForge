import { useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../../auth/AuthContext'
import { AuthForm } from './AuthForm'

export function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as { from?: string } | null)?.from ?? '/'
  return (
    <AuthForm
      mode="login"
      onSubmit={async (email, password) => {
        await login(email, password)
        navigate(from, { replace: true })
      }}
    />
  )
}
