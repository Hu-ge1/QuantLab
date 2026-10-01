import {
  AlertCircle,
  CheckCircle,
  X,
  XCircle,
} from 'lucide-react'
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

type ToastType = 'success' | 'error' | 'info'

interface ToastItem {
  id: string
  type: ToastType
  message: string
}

interface ToastContextValue {
  toast: (type: ToastType, message: string) => void
  success: (message: string) => void
  error: (message: string) => void
  info: (message: string) => void
}

const ToastContext = createContext<ToastContextValue | null>(null)

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast 必须在 ToastProvider 内使用')
  return ctx
}

const ICONS = {
  success: CheckCircle,
  error: XCircle,
  info: AlertCircle,
}
const BORDER = {
  success: 'border-gain/30',
  error: 'border-loss/30',
  info: 'border-accent/30',
}
const ICON_COLOR = {
  success: 'text-gain',
  error: 'text-loss',
  info: 'text-accent',
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])

  const remove = useCallback((id: string) => {
    setItems((prev) => prev.filter((t) => t.id !== id))
  }, [])

  const toast = useCallback(
    (type: ToastType, message: string) => {
      const id = Math.random().toString(36).slice(2)
      setItems((prev) => [...prev, { id, type, message }])
      setTimeout(() => remove(id), 4000)
    },
    [remove],
  )

  const value = useMemo<ToastContextValue>(
    () => ({
      toast,
      success: (m) => toast('success', m),
      error: (m) => toast('error', m),
      info: (m) => toast('info', m),
    }),
    [toast],
  )

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="fixed bottom-4 right-4 z-50 flex max-w-sm flex-col gap-2">
        {items.map((t) => {
          const Icon = ICONS[t.type]
          return (
            <div
              key={t.id}
              className={`flex items-start gap-2.5 rounded-lg border bg-card p-3 shadow-xl animate-slide-up ${BORDER[t.type]}`}
            >
              <Icon size={17} className={`mt-0.5 shrink-0 ${ICON_COLOR[t.type]}`} />
              <div className="min-w-0 flex-1 text-sm text-text-primary">{t.message}</div>
              <button
                onClick={() => remove(t.id)}
                className="shrink-0 text-text-muted hover:text-text-primary"
              >
                <X size={14} />
              </button>
            </div>
          )
        })}
      </div>
    </ToastContext.Provider>
  )
}
