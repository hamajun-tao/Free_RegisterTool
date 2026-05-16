import { useState, useEffect, useCallback, useRef } from 'react'
import { apiFetch } from '@/lib/utils'

export function useApi<T = any>(url: string, deps: any[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const mountedRef = useRef(true)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await apiFetch(url)
      if (mountedRef.current) {
        setData(result as T)
      }
    } catch (e: any) {
      if (mountedRef.current) {
        setError(e?.message || e?.detail || String(e))
      }
    } finally {
      if (mountedRef.current) {
        setLoading(false)
      }
    }
  }, [url])

  useEffect(() => {
    mountedRef.current = true
    load()
    return () => {
      mountedRef.current = false
    }
  }, [load, ...deps])

  return { data, loading, error, reload: load }
}
