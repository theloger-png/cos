import axios from 'axios'

const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_URL ?? 'http://localhost:8090',
  headers: {
    'Content-Type': 'application/json',
  },
})

apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem('cos-token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    const url: string = error.config?.url ?? ''
    if (error.response?.status === 401 && !url.includes('/auth/login')) {
      localStorage.removeItem('cos-token')
      localStorage.removeItem('cos-username')
      localStorage.removeItem('cos-role')
      window.location.href = '/login'
      return Promise.reject(error)
    }
    const detail = error.response?.data?.detail
    let message: string
    if (Array.isArray(detail)) {
      message = detail
        .map((e: { loc?: string[]; msg: string }) => {
          const field = e.loc?.length ? e.loc[e.loc.length - 1] : null
          return field ? `${field}: ${e.msg}` : e.msg
        })
        .join('; ')
    } else {
      message = detail ?? error.message ?? 'Unknown error'
    }
    return Promise.reject(new Error(message))
  },
)

export default apiClient
