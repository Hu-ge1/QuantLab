import { Brain, Loader2, RefreshCw, Search } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { quoteApi, type AnalystResult } from '../lib/api'
import { fmt, fmtPct, pnlColor } from '../lib/utils'
import { useToast } from '../components/Toast'

function normCode(raw: string): string {
  const s = raw.trim().toUpperCase()
  const m = /^(?:SH|SZ|BJ)?(\d{6})(?:\.(SH|SZ|BJ))?$/.exec(s)
  if (!m) return ''
  if (m[2]) return `${m[1]}.${m[2]}`
  if (/^(51|56|58|9)/.test(m[1])) return `${m[1]}.SH`
  if (/^(15|16)/.test(m[1])) return `${m[1]}.SZ`
  if (/^(6|5|9)/.test(m[1])) return `${m[1]}.SH`
  if (/^(0|3)/.test(m[1])) return `${m[1]}.SZ`
  return `${m[1]}.BJ`
}

function MetricCard({
  title,
  date,
  value,
  unit,
  hint,
}: {
  title: string
  date?: string
  value?: number | null
  unit?: string
  hint?: string
}) {
  const empty = value === null || value === undefined
  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <div className="text-xs text-text-secondary">{title}</div>
      {date && <div className="mt-1 text-[11px] text-text-muted">{date}</div>}
      <div className={`mt-2 text-2xl font-semibold num ${empty ? 'text-text-muted' : 'text-text-primary'}`}>
        {empty ? '—' : `${Number(value).toFixed(2)}${unit ?? ''}`}
      </div>
      {hint && <div className="mt-1 text-xs text-text-muted">{hint}</div>}
    </div>
  )
}

export default function AnalystPage() {
  const toast = useToast()
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [data, setData] = useState<AnalystResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [history, setHistory] = useState<AnalystResult[]>([])

  const load = useCallback(async (c?: string) => {
    const target = c || code
    const std = normCode(target)
    if (!std) {
      toast.error('代码格式应为 600519.SH 或 600519')
      return
    }
    setLoading(true)
    try {
      const r = await quoteApi.analyst(std)
      setData(r)
      setHistory((prev) => {
        const next = [r, ...prev.filter((x) => x.code !== r.code)]
        return next.slice(0, 20)
      })
    } catch (e) {
      toast.error(`获取模型分析师数据失败：${e}`)
    } finally {
      setLoading(false)
    }
  }, [code, toast])

  useEffect(() => {
    if (code) {
      const timer = window.setTimeout(() => load(), 500)
      return () => window.clearTimeout(timer)
    }
  }, [code, load])

  const onSearch = () => load()

  return (
    <div className="h-full overflow-y-auto p-5">
      <div className="mx-auto max-w-6xl space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative flex-1 min-w-[240px]">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
            <input
              value={code}
              onChange={(e) => setCode(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && onSearch()}
              placeholder="输入代码：600519.SH 或 600519"
              className="num w-full rounded-lg border border-border bg-card pl-9 pr-3 py-2 text-sm outline-none focus:border-accent/50"
            />
          </div>
          <button
            onClick={onSearch}
            disabled={loading}
            className="flex items-center gap-1.5 rounded-lg border border-border bg-card px-4 py-2 text-sm text-text-secondary hover:text-text-primary disabled:opacity-50"
          >
            {loading ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
            查询
          </button>
          <button
            onClick={() => data && load(data.code)}
            disabled={!data || loading}
            className="flex items-center gap-1.5 rounded-lg border border-border bg-card px-4 py-2 text-sm text-text-secondary hover:text-text-primary disabled:opacity-50"
          >
            <RefreshCw size={14} />
            刷新
          </button>
        </div>

        {data && (
          <div className="flex flex-wrap items-center gap-2 text-xs text-text-muted">
            <span className="rounded border border-border bg-surface px-2 py-1">
              标的：<span className="text-text-primary">{data.name || data.code}</span>
            </span>
            <span className="rounded border border-border bg-surface px-2 py-1">
              代码：<span className="num text-text-primary">{data.code}</span>
            </span>
            <span className="rounded border border-border bg-surface px-2 py-1">数据源：{data.source}</span>
            {data.note && (
              <span className="rounded border border-accent/30 bg-accent/10 px-2 py-1 text-accent">
                {data.note}
              </span>
            )}
          </div>
        )}

        {data ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <MetricCard
              title="参与意愿"
              date={data.desire?.date}
              value={data.desire?.value}
              hint={
                data.desire
                  ? `5日均值 ${data.desire.avg_5d ?? '—'} · 变化 ${(data.desire.change ?? 0) > 0 ? '+' : ''}${data.desire.change ?? 0}`
                  : undefined
              }
            />
            <MetricCard
              title="用户关注度"
              date={data.focus?.date}
              value={data.focus?.value}
            />
            <MetricCard
              title="综合评分"
              date={data.score?.date}
              value={data.score?.value}
            />
            <MetricCard
              title="机构参与度"
              date={data.institution?.date}
              value={data.institution?.value}
            />
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center rounded-xl border border-border bg-card p-10 text-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-xl border border-accent/40 bg-accent/15">
              <Brain size={22} className="text-accent" />
            </div>
            <h3 className="mt-4 text-sm font-medium">输入股票代码查看模型分析师数据</h3>
            <p className="mt-1 max-w-md text-xs leading-5 text-text-secondary">
              聚合东方财富股吧评分、机构参与度、用户关注度等指标，数据来自 akshare 公开接口。
            </p>
          </div>
        )}

        {history.length > 0 && (
          <div className="rounded-xl border border-border bg-card p-4">
            <h3 className="text-sm font-medium">最近查询</h3>
            <div className="mt-3 flex flex-wrap gap-2">
              {history.map((h) => (
                <button
                  key={h.code}
                  onClick={() => {
                    setCode(h.code)
                    setData(h)
                  }}
                  className={`rounded-lg border px-3 py-1.5 text-xs num ${
                    data?.code === h.code
                      ? 'border-accent/40 bg-accent/15 text-accent'
                      : 'border-border text-text-secondary hover:text-text-primary'
                  }`}
                >
                  {h.code}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
