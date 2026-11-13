import { useNavigate } from 'react-router-dom'
import { useAuth } from '../../auth/AuthContext'
import { AuthForm } from './AuthForm'

export function RegisterPage() {
  const { register } = useAuth()
  const navigate = useNavigate()
  return (
    <AuthForm
      mode="register"
      onSubmit={async (email, password) => {
        await register(email, password)
        navigate('/onboarding', { replace: true })
      }}
    />
  )
}
