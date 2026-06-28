'use client'

import { useCallback, useEffect, useState } from 'react'
import { ApiError } from './api'

export interface AsyncState<T> {
  data: T | null
  loading: boolean
  error: string | null
  reload: () => void
}

// Runs an async loader on mount (and whenever `deps` change). Returns
// {data, loading, error, reload}. `loader` should be stable or depend only
// on `deps` — pass the values it closes over in `deps`.
export function useApi<T>(loader: () => Promise<T>, deps: unknown[] = []): AsyncState<T> {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [tick, setTick] = useState(0)

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(loader, deps)

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError(null)
    run()
      .then((res) => {
        if (alive) setData(res)
      })
      .catch((e) => {
        if (alive) setError(e instanceof ApiError ? e.message : String(e))
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [run, tick])

  const reload = useCallback(() => setTick((t) => t + 1), [])
  return { data, loading, error, reload }
}
