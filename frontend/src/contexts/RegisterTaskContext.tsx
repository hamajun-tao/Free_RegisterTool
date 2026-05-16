import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useCallback,
  type ReactNode,
} from 'react'
import { apiFetch, API_BASE, getToken } from '@/lib/utils'

const ACTIVE_TASK_KEY = 'register_task_active_id'
const DISMISSED_TASK_KEY = 'register_task_dismissed_id'
const FALLBACK_POLL_INTERVAL_MS = 3000

export type RegisterTask = {
  id?: string
  task_id?: string
  status?: string
  progress?: string
  total?: number
  started?: number
  completed?: number
  success?: number
  skipped?: number
  errors?: string[]
  error?: string
  worker_states?: any[]
  cashier_urls?: string[]
  [key: string]: unknown
}

function isTaskFinished(task: RegisterTask | null | undefined) {
  return (
    task?.status === 'done' ||
    task?.status === 'failed' ||
    task?.status === 'stopped'
  )
}

function pickLatestActiveTask(tasks: RegisterTask[]) {
  const activeTasks = (Array.isArray(tasks) ? tasks : []).filter(
    (item) => item && !isTaskFinished(item),
  )
  if (activeTasks.length === 0) return null

  const pickByTaskId = [...activeTasks].sort((a, b) => {
    const aId = String(a?.task_id || a?.id || '')
    const bId = String(b?.task_id || b?.id || '')
    return bId.localeCompare(aId, undefined, { numeric: true })
  })[0]

  if (pickByTaskId?.task_id || pickByTaskId?.id) {
    return pickByTaskId
  }

  return activeTasks.sort((a, b) => {
    const aValue = Number(
      a?.updated_at || a?.finished_at || a?.started_at || a?.created_at || 0,
    )
    const bValue = Number(
      b?.updated_at || b?.finished_at || b?.started_at || b?.created_at || 0,
    )
    return bValue - aValue
  })[0]
}

function readLocalStorage(key: string) {
  try {
    return localStorage.getItem(key) || ''
  } catch {
    return ''
  }
}

function writeLocalStorage(key: string, value: string) {
  try {
    if (value) {
      localStorage.setItem(key, value)
      return
    }
    localStorage.removeItem(key)
  } catch {
    /* ignored */
  }
}

type RegisterTaskContextValue = {
  task: RegisterTask | null
  polling: boolean
  startTask: (initial: RegisterTask) => void
  clearTask: () => void
  setPaused: (p: boolean) => void
}

const RegisterTaskContext = createContext<RegisterTaskContextValue | null>(null)

export function RegisterTaskProvider({ children }: { children: ReactNode }) {
  const [task, setTask] = useState<RegisterTask | null>(null)
  const [polling, setPolling] = useState(false)
  const [paused, setPaused] = useState(false)
  const fallbackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const eventSourceRef = useRef<EventSource | null>(null)
  const subscribedIdRef = useRef('')
  const cashierShownRef = useRef<Set<string>>(new Set())
  const restoreGenerationRef = useRef(0)
  const clearedRef = useRef(false)

  const closeStreams = useCallback(() => {
    if (eventSourceRef.current) {
      try {
        eventSourceRef.current.close()
      } catch {
        /* ignored */
      }
      eventSourceRef.current = null
    }
    if (fallbackTimerRef.current) {
      clearTimeout(fallbackTimerRef.current)
      fallbackTimerRef.current = null
    }
  }, [])

  const stop = useCallback(() => {
    closeStreams()
    subscribedIdRef.current = ''
    setPolling(false)
  }, [closeStreams])

  const handleFinalSnapshot = useCallback((snap: RegisterTask) => {
    const finishedId = String(snap?.task_id || snap?.id || '').trim()
    if (Array.isArray(snap.cashier_urls)) {
      for (const url of snap.cashier_urls) {
        if (url && !cashierShownRef.current.has(url)) {
          cashierShownRef.current.add(url)
          try {
            window.open(url, '_blank')
          } catch {
            /* ignored */
          }
        }
      }
    }
    writeLocalStorage(ACTIVE_TASK_KEY, '')
    if (finishedId && readLocalStorage(DISMISSED_TASK_KEY) === finishedId) {
      writeLocalStorage(DISMISSED_TASK_KEY, '')
    }
  }, [])

  const fallbackTick = useCallback(
    async (id: string) => {
      if (clearedRef.current || !id || subscribedIdRef.current !== id) return
      try {
        const t: RegisterTask = await apiFetch(`/tasks/${id}?include_logs=0`)
        if (clearedRef.current || subscribedIdRef.current !== id) return
        setTask(t)
        if (isTaskFinished(t)) {
          handleFinalSnapshot(t)
          stop()
          return
        }
      } catch {
        /* ignored */
      }
      if (!clearedRef.current && subscribedIdRef.current === id) {
        fallbackTimerRef.current = setTimeout(
          () => fallbackTick(id),
          FALLBACK_POLL_INTERVAL_MS,
        )
      }
    },
    [handleFinalSnapshot, stop],
  )

  const subscribe = useCallback(
    (id: string) => {
      if (clearedRef.current || !id) return
      closeStreams()
      subscribedIdRef.current = id
      setPolling(true)

      const token = getToken()
      const url =
        `${API_BASE}/tasks/${encodeURIComponent(id)}/progress/stream` +
        (token ? `?token=${encodeURIComponent(token)}` : '')

      try {
        const es = new EventSource(url, { withCredentials: false })
        eventSourceRef.current = es

        es.onmessage = (ev) => {
          if (clearedRef.current || subscribedIdRef.current !== id) return
          try {
            const data = JSON.parse(ev.data || '{}') as Partial<RegisterTask> & {
              gone?: boolean
              final?: boolean
            }
            if (data.gone) {
              stop()
              return
            }
            if (data && typeof data === 'object') {
              setTask((prev) => ({ ...(prev || {}), ...data }))
              if (data.final || isTaskFinished(data as RegisterTask)) {
                handleFinalSnapshot(data as RegisterTask)
                stop()
              }
            }
          } catch {
            /* ignored */
          }
        }

        es.onerror = () => {
          if (clearedRef.current || subscribedIdRef.current !== id) return
          try {
            es.close()
          } catch {
            /* ignored */
          }
          eventSourceRef.current = null
          if (fallbackTimerRef.current) clearTimeout(fallbackTimerRef.current)
          fallbackTimerRef.current = setTimeout(() => fallbackTick(id), 800)
        }
      } catch {
        if (fallbackTimerRef.current) clearTimeout(fallbackTimerRef.current)
        fallbackTimerRef.current = setTimeout(() => fallbackTick(id), 0)
      }
    },
    [closeStreams, fallbackTick, handleFinalSnapshot, stop],
  )

  const startTask = useCallback(
    (initial: RegisterTask) => {
      const id = initial?.task_id || initial?.id
      if (!id) {
        console.error('startTask: no task_id in response', initial)
        return
      }
      clearedRef.current = false
      const normalizedId = String(id)
      cashierShownRef.current = new Set()
      setTask(initial)
      writeLocalStorage(ACTIVE_TASK_KEY, normalizedId)
      writeLocalStorage(DISMISSED_TASK_KEY, '')
      subscribe(normalizedId)
    },
    [subscribe],
  )

  const clearTask = useCallback(() => {
    clearedRef.current = true
    const dismissedId = String(task?.task_id || task?.id || '').trim()
    stop()
    setTask(null)
    writeLocalStorage(ACTIVE_TASK_KEY, '')
    writeLocalStorage(DISMISSED_TASK_KEY, dismissedId)
  }, [stop, task])

  useEffect(() => {
    let cancelled = false
    const generation = ++restoreGenerationRef.current
    const dismissedId = readLocalStorage(DISMISSED_TASK_KEY)
    const activeId = readLocalStorage(ACTIVE_TASK_KEY)

    if (activeId) {
      ;(async () => {
        try {
          const t: RegisterTask = await apiFetch(`/tasks/${activeId}?include_logs=0`)
          if (cancelled || restoreGenerationRef.current !== generation) return
          if (isTaskFinished(t)) {
            writeLocalStorage(ACTIVE_TASK_KEY, '')
            if (dismissedId && dismissedId === activeId) {
              writeLocalStorage(DISMISSED_TASK_KEY, '')
            }
            return
          }
          if (dismissedId && dismissedId === activeId) return
          if (clearedRef.current) return
          setTask(t)
          subscribe(activeId)
        } catch {
          writeLocalStorage(ACTIVE_TASK_KEY, '')
        }
      })()
    }

    ;(async () => {
      try {
        const tasks = (await apiFetch('/tasks')) as RegisterTask[]
        if (cancelled || restoreGenerationRef.current !== generation) return
        const latestActiveTask = pickLatestActiveTask(tasks)
        if (!latestActiveTask) return
        const latestActiveTaskId = String(
          latestActiveTask.task_id || latestActiveTask.id || '',
        ).trim()
        if (!latestActiveTaskId) return
        if (dismissedId && dismissedId === latestActiveTaskId) return
        if (clearedRef.current) return
        setTask(latestActiveTask)
        writeLocalStorage(ACTIVE_TASK_KEY, latestActiveTaskId)
        subscribe(latestActiveTaskId)
      } catch {
        /* ignored */
      }
    })()

    return () => {
      cancelled = true
    }
  }, [subscribe])

  useEffect(() => {
    if (paused || clearedRef.current) {
      closeStreams()
      return
    }
    const targetId = String(task?.task_id || task?.id || '').trim()
    if (!targetId) return
    if (subscribedIdRef.current === targetId && (eventSourceRef.current || fallbackTimerRef.current)) {
      return
    }
    subscribe(targetId)
  }, [paused, closeStreams, subscribe, task])

  useEffect(() => {
    return () => {
      closeStreams()
    }
  }, [closeStreams])

  const value = useMemo<RegisterTaskContextValue>(
    () => ({ task, polling, startTask, clearTask, setPaused }),
    [task, polling, startTask, clearTask],
  )

  return (
    <RegisterTaskContext.Provider value={value}>
      {children}
    </RegisterTaskContext.Provider>
  )
}

export function useRegisterTask(): RegisterTaskContextValue {
  const ctx = useContext(RegisterTaskContext)
  if (!ctx) {
    throw new Error('useRegisterTask must be used within RegisterTaskProvider')
  }
  return ctx
}
