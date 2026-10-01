import {
  Bot,
  ChevronDown,
  ChevronRight,
  Database,
  Dna,
  Brain,
  Delete,
  Loader2,
  Plug,
  Plus,
  RefreshCw,
  Search,
  Send,
  TrendingUp,
  User,
  Wallet,
  Wrench,
  X,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { chatApi, type ChatMessage, type ChatSession } from '../lib/api'
import { relativeTime } from '../lib/utils'

interface ToolCall {
  callId: string
  tool: string
  args: Record<string, unknown>
  status: 'running' | 'done' | 'error'
}
interface DisplayMessage extends ChatMessage {
  toolCalls?: ToolCall[]
  thinking?: boolean
}

const TOOL_META: Record<string, { icon: typeof Search; label: (a: Record<string, unknown>) => string }> = {
  ql_search: { icon: Search, label: (a) => `搜索：${a.query ?? ''}` },
  ql_get_price: { icon: TrendingUp, label: (a) => `${a.security ?? ''} K线×${a.count ?? 60}` },
  ql_get_fundamentals: { icon: Database, label: (a) => `${a.security ?? ''} 基本面` },
  ql_get_index_stocks: { icon: Dna, label: (a) => `${a.index ?? ''} 成分股` },
  ql_screen_stocks: { icon: Search, label: (a) => `批量选股：${a.profile ?? '均衡'} · Top ${a.limit ?? 10}` },
  ql_diagnose_basket: { icon: TrendingUp, label: (a) => `候选组合历史体检：${(a.codes as string[] | undefined)?.length ?? 0}只` },
  portfolio_get: { icon: Wallet, label: () => '读取 QuantLab 持仓' },
  qmt_account: { icon: Plug, label: () => 'QMT 实盘账户（只读）' },
  qmt_realtime: { icon: TrendingUp, label: (a) => `QMT 实时价：${(a.codes as string[] | undefined)?.join(' ') ?? ''}` },
  bayesian_decision: { icon: Brain, label: (a) => `贝叶斯分析：${String(a.hypothesis ?? '').slice(0, 18)}` },
  memory_save: { icon: Database, label: (a) => `记忆：${a.key ?? ''}` },
}

const PRESETS = [
  '帮我看看贵州茅台最近走势怎么样？',
  '用贝叶斯方法分析：现在要不要加仓沪深300？',
  '600519.SH 现在的估值贵不贵？',
  '记住我偏好中低风险、长线价值投资',
]

export default function ChatPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [activeId, setActiveId] = useState<string>('')
  const [messages, setMessages] = useState<DisplayMessage[]>([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [loadingMsgs, setLoadingMsgs] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const isStreamingRef = useRef(false)

  useEffect(() => {
    const prompt = searchParams.get('prompt')
    if (prompt) {
      setInput(prompt)
      setSearchParams({}, { replace: true })
    }
  }, [searchParams, setSearchParams])

  const loadSessions = useCallback(async () => {
    try {
      setSessions(await chatApi.sessions())
    } catch {
      /* 后端未启动时静默 */
    }
  }, [])

  useEffect(() => {
    loadSessions()
  }, [loadSessions])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // 页面卸载时中止正在进行的流式请求，避免内存泄漏
  useEffect(() => {
    return () => {
      abortRef.current?.abort()
    }
  }, [])

  // 回到页面时，若有活跃会话则自动重载消息
  useEffect(() => {
    if (activeId) {
      openSession(activeId)
    }
  }, [])

  const openSession = useCallback(async (id: string) => {
    if (isStreamingRef.current) return
    setActiveId(id)
    setLoadingMsgs(true)
    try {
      const msgs = await chatApi.messages(id)
      setMessages(msgs.map((m) => ({ ...m })))
    } catch {
      setMessages([])
    } finally {
      setLoadingMsgs(false)
    }
  }, [])

  // 回到页面时，若有活跃会话则自动重载消息
  useEffect(() => {
    if (activeId) {
      openSession(activeId)
    }
  }, [activeId, openSession])

  const newSession = async () => {
    if (isStreamingRef.current) return
    setActiveId('')
    setMessages([])
  }

  const deleteSession = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation()
    await chatApi.deleteSession(id).catch(() => {})
    if (activeId === id) {
      setActiveId('')
      setMessages([])
    }
    loadSessions()
  }

  const updateLastAssistant = (updater: (m: DisplayMessage) => DisplayMessage) => {
    setMessages((prev) => {
      const copy = [...prev]
      for (let i = copy.length - 1; i >= 0; i--) {
        if (copy[i].role === 'assistant') {
          copy[i] = updater(copy[i])
          break
        }
      }
      return copy
    })
  }

  const send = async (text?: string) => {
    const content = (text ?? input).trim()
    if (!content || streaming) return
    setInput('')
    setStreaming(true)
    isStreamingRef.current = true
    const ab = new AbortController()
    abortRef.current = ab

    let sid = activeId
    try {
      if (!sid) {
        const { session_id } = await chatApi.newSession()
        sid = session_id
        setActiveId(sid)
      }
    } catch (e) {
      setStreaming(false)
      isStreamingRef.current = false
      updateLastAssistant(() => ({ role: 'assistant', content: `❌ 无法连接后端服务：${e}` }))
      return
    }

    setMessages((prev) => [
      ...prev,
      { role: 'user', content },
      { role: 'assistant', content: '', thinking: true, toolCalls: [] },
    ])

    try {
      const res = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sid, message: content }),
        signal: ab.signal,
      })
      const reader = res.body!.getReader()
      const dec = new TextDecoder()
      let buf = ''
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop() || ''
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          let ev: Record<string, unknown>
          try {
            ev = JSON.parse(line.slice(6))
          } catch {
            continue
          }
          switch (ev.type) {
            case 'session_id':
              loadSessions()
              break
            case 'thinking':
              updateLastAssistant((m) => ({ ...m, thinking: true }))
              break
            case 'tool_call':
              updateLastAssistant((m) => ({
                ...m,
                thinking: false,
                toolCalls: [
                  ...(m.toolCalls || []),
                  {
                    callId: String(ev.call_id),
                    tool: String(ev.tool),
                    args: (ev.args as Record<string, unknown>) || {},
                    status: 'running',
                  },
                ],
              }))
              break
            case 'tool_result':
              updateLastAssistant((m) => ({
                ...m,
                toolCalls: (m.toolCalls || []).map((t) =>
                  t.callId === ev.call_id
                    ? { ...t, status: ev.ok ? ('done' as const) : ('error' as const) }
                    : t,
                ),
              }))
              break
            case 'token':
              updateLastAssistant((m) => ({
                ...m,
                thinking: false,
                content: m.content + String(ev.text ?? ''),
              }))
              break
            case 'error':
              updateLastAssistant((m) =>
                m.content
                  ? m
                  : { ...m, thinking: false, content: `❌ ${String(ev.text ?? '')}` },
              )
              break
          }
        }
      }
    } catch (e) {
      if ((e as Error).name !== 'AbortError') {
        updateLastAssistant((m) =>
          m.content ? m : { ...m, content: `❌ 连接中断：${e}` },
        )
      }
    } finally {
      setStreaming(false)
      isStreamingRef.current = false
      abortRef.current = null
      loadSessions()
    }
  }

  const resumeLast = async () => {
    // 找到最后一个 assistant 消息且状态为 thinking 的，取它前一条 user 消息
    let lastUserContent = ''
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i]
      if (m.role === 'assistant' && m.thinking && !m.content) {
        // 向前找最近一条 user 消息
        for (let j = i - 1; j >= 0; j--) {
          if (messages[j].role === 'user') {
            lastUserContent = messages[j].content
            break
          }
        }
        break
      }
    }
    if (lastUserContent) {
      await send(lastUserContent)
    }
  }

  const stop = () => {
    abortRef.current?.abort()
  }

  return (
    <div className="flex h-full">
      {/* 会话列表 */}
      <div className="hidden w-64 shrink-0 flex-col border-r border-border bg-surface/40 md:flex">
        <div className="p-3">
          <button
            onClick={newSession}
            className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-accent/40 bg-accent/15 py-2 text-sm text-accent hover:bg-accent/25"
          >
            <Plus size={15} /> 新对话
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
          {sessions.map((s) => (
            <div
              key={s.id}
              onClick={() => openSession(s.id)}
              className={`group mb-1 flex cursor-pointer items-center justify-between rounded-lg px-3 py-2 text-sm ${
                activeId === s.id
                  ? 'border-r-2 border-accent bg-card text-text-primary'
                  : 'text-text-secondary hover:bg-card'
              }`}
            >
              <div className="min-w-0">
                <div className="truncate">{s.title || '新对话'}</div>
                <div className="text-[11px] text-text-muted">{relativeTime(s.updated_at)}</div>
              </div>
              <button
                onClick={(e) => deleteSession(s.id, e)}
                className="ml-1 hidden shrink-0 text-text-muted hover:text-loss group-hover:block"
              >
                <Delete size={14} />
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* 消息区 */}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-6">
          <div className="mx-auto max-w-3xl space-y-4">
            {messages.length === 0 && (
              <div className="flex flex-col items-center pt-16 text-center">
                <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-accent/40 bg-accent/15">
                  <Bot size={26} className="text-accent" />
                </div>
                <h2 className="mt-4 text-lg font-semibold">和量化研究助手聊聊</h2>
                <p className="mt-1 text-sm text-text-secondary">
                  AI 会调用真实行情接口回答，决策类问题自动走贝叶斯分析
                </p>
                <div className="mt-6 grid w-full max-w-lg grid-cols-1 gap-2 sm:grid-cols-2">
                  {PRESETS.map((p) => (
                    <button
                      key={p}
                      onClick={() => setInput(p)}
                      className="rounded-lg border border-border bg-card px-3 py-2.5 text-left text-[13px] text-text-secondary hover:border-accent/40 hover:text-text-primary"
                    >
                      {p}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {loadingMsgs && (
              <div className="flex justify-center py-8">
                <Loader2 size={20} className="animate-spin text-text-muted" />
              </div>
            )}

            {messages.map((m, i) => (
              <div key={i} className={`flex gap-3 ${m.role === 'user' ? 'justify-end' : ''}`}>
                {m.role === 'assistant' && (
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-teal/40 bg-teal/15">
                    <Bot size={16} className="text-teal" />
                  </div>
                )}
                <div className={`max-w-[85%] ${m.role === 'user' ? 'order-first' : ''}`}>
                  {m.role === 'assistant' && m.toolCalls && m.toolCalls.length > 0 && (
                    <ToolCallPanel calls={m.toolCalls} />
                  )}
                  {(m.content || !m.thinking) && (
                    <div
                      className={`rounded-xl px-4 py-2.5 ${
                        m.role === 'user'
                          ? 'rounded-tr-sm border border-accent/30 bg-accent/20'
                          : 'rounded-tl-sm border border-border bg-card'
                      }`}
                    >
                      {m.role === 'assistant' ? (
                        <div className={`prose-dark ${streaming && i === messages.length - 1 && !m.content ? 'streaming-cursor' : ''}`}>
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
                        </div>
                      ) : (
                        <div className="whitespace-pre-wrap text-sm">{m.content}</div>
                      )}
                    </div>
                  )}
                  {m.thinking && !m.content && (!m.toolCalls || m.toolCalls.length === 0) && (
                    <div className="flex items-center gap-2 rounded-xl rounded-tl-sm border border-border bg-card px-4 py-3 text-sm text-text-secondary">
                      <Loader2 size={14} className="animate-spin" /> 正在思考…
                    </div>
                  )}
                </div>
                {m.role === 'user' && (
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-accent/40 bg-accent/20">
                    <User size={15} className="text-accent" />
                  </div>
                )}
              </div>
            ))}
            <div ref={bottomRef} />
          </div>
        </div>

        {/* 输入区 */}
        <div className="border-t border-border bg-surface/60 p-4">
          <div className="mx-auto flex max-w-3xl items-end gap-2">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  send()
                }
              }}
              placeholder="输入问题，Enter 发送，Shift+Enter 换行"
              rows={1}
              className="max-h-40 min-h-[42px] flex-1 resize-none rounded-xl border border-border bg-card px-4 py-2.5 text-sm outline-none placeholder:text-text-muted focus:border-accent/50"
              style={{ height: Math.min(160, 42 + input.split('\n').length * 20) }}
            />
            {streaming ? (
              <button
                onClick={stop}
                className="flex h-[42px] w-[42px] items-center justify-center rounded-xl border border-loss/40 bg-loss/15 text-loss hover:bg-loss/25"
                title="停止"
              >
                <X size={17} />
              </button>
            ) : (
              <>
                <button
                  onClick={resumeLast}
                  disabled={!activeId || streaming}
                  className="flex h-[42px] w-[42px] items-center justify-center rounded-xl border border-border bg-card text-text-secondary hover:text-text-primary disabled:opacity-40"
                  title="继续上一次未完成的回答"
                >
                  <RefreshCw size={16} />
                </button>
                <button
                  onClick={() => send()}
                  disabled={!input.trim()}
                  className="flex h-[42px] w-[42px] items-center justify-center rounded-xl bg-accent text-white disabled:opacity-40"
                >
                  <Send size={16} />
                </button>
              </>
            )}
          </div>
          <p className="mx-auto mt-2 max-w-3xl text-center text-[11px] text-text-muted">
            所有分析仅供研究参考，不构成投资建议
          </p>
        </div>
      </div>
    </div>
  )
}

function ToolCallPanel({ calls }: { calls: ToolCall[] }) {
  const [open, setOpen] = useState(true)
  const running = calls.some((c) => c.status === 'running')
  return (
    <div className="mb-2 rounded-lg border border-border bg-surface/70">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-xs text-text-secondary"
      >
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        <Wrench size={13} className="text-teal" />
        {running ? '正在查询数据…' : `已调用 ${calls.length} 个工具`}
      </button>
      {open && (
        <div className="space-y-1 px-3 pb-2">
          {calls.map((c) => {
            const meta = TOOL_META[c.tool] || { icon: Wrench, label: () => c.tool }
            const Icon = meta.icon
            return (
              <div key={c.callId} className="flex items-center justify-between text-xs">
                <span className="flex min-w-0 items-center gap-1.5 font-mono text-text-secondary">
                  <Icon size={12} className="shrink-0 text-teal" />
                  <span className="truncate">{meta.label(c.args)}</span>
                </span>
                {c.status === 'running' && (
                  <Loader2 size={12} className="shrink-0 animate-spin text-text-muted" />
                )}
                {c.status === 'done' && (
                  <span className="shrink-0 text-gain">✓</span>
                )}
                {c.status === 'error' && (
                  <span className="shrink-0 text-loss">✗</span>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
