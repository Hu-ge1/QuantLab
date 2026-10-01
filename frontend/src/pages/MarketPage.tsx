import { Globe2, Loader2, RefreshCw, Sparkles } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import {
  Area,
  AreaChart,
  ResponsiveContainer,
} from 'recharts'
import {
  marketApi,
  quoteApi,
  type KlineBar,
  type MarketOverview,
  type MarketSector,
} from '../lib/api'
import { fmt, fmtPct, pnlColor } from '../lib/utils'

const COLOR = { gain: '#22c55e', loss: '#ef4444', flat: '#94a3b8' }

function trendColor(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v) || v === 0) return COLOR.flat
  return v > 0 ? COLOR.gain : COLOR.loss
}

/** 涨跌幅 → 热力 tile 背景色（±3% 封顶的透明度分档） */
function heatStyle(chg: number): React.CSSProperties {
  const clamped = Math.max(-3, Math.min(3, chg))
  const alpha = 0.08 + (Math.abs(clamped) / 3) * 0.4
  return chg >= 0
    ? { background: `rgba(34, 197, 94, ${alpha.toFixed(3)})` }
    : { background: `rgba(239, 68, 68, ${alpha.toFixed(3)})` }
}

/** 新浪代码 sz002623 → 002623.SZ */
function toStdCode(sinaCode: string): string {
  const m = /^([a-z]{2})(\d{6})$/i.exec(sinaCode.trim())
  if (!m) return sinaCode
  const suffix = m[1].toLowerCase() === 'sh' ? 'SH' : m[1].toLowerCase() === 'sz' ? 'SZ' : 'BJ'
  return `${m[2]}.${suffix}`
}

export default function MarketPage() {
  const [data, setData] = useState<MarketOverview | null>(null)
  const [loading, setLoading] = useState(true)
  const [selSector, setSelSector] = useState<MarketSector | null>(null)
  const [leaderBars, setLeaderBars] = useState<KlineBar[]>([])
  const [leaderLoading, setLeaderLoading] = useState(false)

  const load = useCallback(async (force = false) => {
    setLoading(true)
    try {
      setData(await marketApi.overview(force))
    } catch {
      /* 静默 */
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const pickSector = async (s: MarketSector) => {
    if (selSector?.name === s.name) return
    setSelSector(s)
    setLeaderBars([])
    setLeaderLoading(true)
    try {
      const r = await quoteApi.kline(toStdCode(s.leader_code), 'daily', 60)
      setLeaderBars(r.bars || [])
    } catch {
      setLeaderBars([])
    } finally {
      setLeaderLoading(false)
    }
  }

  const act = data?.activity
  const breadthPct = act?.breadth !== null && act?.breadth !== undefined ? act.breadth * 100 : null
  const sectors = data?.sectors ?? []
  const sortedSectors = [...sectors].sort((a, b) => b.change_pct - a.change_pct)

  return (
    <div className="h-full overflow-y-auto p-5">
      <div className="mx-auto max-w-6xl space-y-4">
        {/* 操作条 */}
        <div className="flex items-center justify-between">
          <span className="text-xs text-text-muted">
            {data ? `数据截至 ${act?.date || data.generated_at} · ${data.source}` : '加载中…'}
          </span>
          <button
            onClick={() => load(true)}
            disabled={loading}
            className="flex items-center gap-1.5 rounded-lg border border-teal/40 bg-teal/15 px-3 py-2 text-sm text-teal hover:bg-teal/25 disabled:opacity-50"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> 刷新
          </button>
        </div>

        {/* 骨架屏 */}
        {loading && !data && (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
              {[...Array(6)].map((_, i) => (
                <div key={i} className="h-32 animate-pulse rounded-xl border border-border bg-card" />
              ))}
            </div>
            <div className="h-48 animate-pulse rounded-xl border border-border bg-card" />
          </div>
        )}

        {/* 指数看板 */}
        {data && (
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
            {data.indices.map((idx) => (
              <div
                key={idx.code}
                className="rounded-xl border border-border bg-card p-4 transition hover:border-teal/40"
              >
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium">{idx.name}</span>
                  <span className="text-[11px] text-text-muted">{idx.date.slice(5)}</span>
                </div>
                <div className={`num mt-2 text-2xl font-semibold ${pnlColor(idx.change_pct)}`}>
                  {fmt(idx.close, 2)}
                </div>
                <div className="mt-1 flex items-center gap-3 text-xs">
                  <span className={`num ${pnlColor(idx.change_pct)}`}>{fmtPct(idx.change_pct)}</span>
                  {idx.chg_5d !== null && (
                    <span className="num text-text-muted">5日 {fmtPct(idx.chg_5d)}</span>
                  )}
                  {idx.chg_20d !== null && (
                    <span className={`num ${pnlColor(idx.chg_20d)}`}>20日 {fmtPct(idx.chg_20d)}</span>
                  )}
                </div>
                {idx.spark && idx.spark.length > 2 && (
                  <div className="mt-2" style={{ height: 46 }}>
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={idx.spark.map((c, i) => ({ i, c }))}>
                        <Area
                          type="monotone"
                          dataKey="c"
                          stroke={trendColor(idx.chg_20d)}
                          fill={trendColor(idx.chg_20d)}
                          fillOpacity={0.12}
                          strokeWidth={1.5}
                        />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {/* 市场宽度 */}
        {act && (
          <div className="rounded-xl border border-border bg-card p-5">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-sm font-medium">市场宽度（当日全 A 统计）</h3>
              <span className="num text-sm text-teal">活跃度 {act.active_pct.toFixed(1)}%</span>
            </div>
            {breadthPct !== null && (
              <div className="mb-4 flex h-6 overflow-hidden rounded-lg text-xs">
                <div
                  className="flex items-center justify-center bg-gain/60 text-white transition-all"
                  style={{ width: `${breadthPct}%` }}
                >
                  上涨 {fmt(act.up, 0)}
                </div>
                <div
                  className="flex items-center justify-center bg-loss/60 text-white"
                  style={{ width: `${100 - breadthPct}%` }}
                >
                  下跌 {fmt(act.down, 0)}
                </div>
              </div>
            )}
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <Stat label="涨停" value={fmt(act.limit_up, 0)} color="text-gain" note={`（真实涨停 ${fmt(act.real_limit_up ?? 0, 0)}）`} />
              <Stat label="跌停" value={fmt(act.limit_down, 0)} color="text-loss" />
              <Stat label="平盘" value={fmt(act.flat ?? 0, 0)} />
              <Stat label="停牌" value={fmt(act.suspended ?? 0, 0)} />
            </div>
          </div>
        )}

        {/* 板块热力网格 */}
        {sortedSectors.length > 0 && (
          <div className="rounded-xl border border-border bg-card p-5">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-sm font-medium">行业板块热力图（{sortedSectors.length} 个板块）</h3>
              <span className="text-[11px] text-text-muted">颜色深浅 = 涨跌幅度 · 点击板块看领涨股走势</span>
            </div>
            <div className="grid grid-cols-3 gap-1.5 sm:grid-cols-5 md:grid-cols-8">
              {sortedSectors.map((s) => (
                <button
                  key={s.name}
                  onClick={() => pickSector(s)}
                  style={heatStyle(s.change_pct)}
                  className={`rounded-lg border px-1.5 py-2 text-center transition hover:border-teal/60 ${
                    selSector?.name === s.name ? 'border-teal ring-1 ring-teal/50' : 'border-transparent'
                  }`}
                  title={`${s.name} ${fmtPct(s.change_pct)} · 领涨股 ${s.leader} ${fmtPct(s.leader_chg)}`}
                >
                  <div className="truncate text-[11px] font-medium">{s.name}</div>
                  <div className={`num text-[11px] font-semibold ${pnlColor(s.change_pct)}`}>
                    {fmtPct(s.change_pct)}
                  </div>
                </button>
              ))}
            </div>

            {/* 领涨股走势（点击板块懒加载） */}
            {selSector && (
              <div className="mt-4 rounded-lg border border-border bg-surface p-4">
                <div className="mb-2 flex items-center gap-2 text-sm">
                  <span className="font-medium">{selSector.name}</span>
                  <span className={`num ${pnlColor(selSector.change_pct)}`}>{fmtPct(selSector.change_pct)}</span>
                  <span className="text-text-muted">· 领涨股</span>
                  <span className="num text-teal">{toStdCode(selSector.leader_code)}</span>
                  <span>{selSector.leader}</span>
                  <span className={`num ${pnlColor(selSector.leader_chg)}`}>{fmtPct(selSector.leader_chg)}</span>
                  <span className="text-[11px] text-text-muted">近 60 个交易日</span>
                  {leaderLoading && <Loader2 size={13} className="animate-spin text-text-muted" />}
                </div>
                {leaderBars.length > 2 ? (
                  <div style={{ height: 160 }}>
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={leaderBars.map((b) => ({ d: b.date.slice(5), c: b.close }))}>
                        <Area
                          type="monotone"
                          dataKey="c"
                          stroke={trendColor(
                            leaderBars[leaderBars.length - 1].close / leaderBars[0].close - 1 < 0
                              ? -1
                              : 1,
                          )}
                          fill="#14b8a6"
                          fillOpacity={0.12}
                          strokeWidth={1.8}
                        />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                ) : (
                  leaderLoading && (
                    <div className="py-6 text-center text-xs text-text-muted">
                      正在拉取 {toStdCode(selSector.leader_code)} 的日K（新浪源，首次约 2~4 秒）…
                    </div>
                  )
                )}
              </div>
            )}
          </div>
        )}

        {/* 自动复盘要点 */}
        {data?.conclusions && data.conclusions.length > 0 && (
          <div className="rounded-xl border border-accent/30 bg-gradient-to-br from-accent/10 to-transparent p-5">
            <div className="mb-2 flex items-center gap-2 text-sm font-medium">
              <Sparkles size={15} className="text-accent" />
              自动复盘要点
              <span className="text-[11px] font-normal text-text-muted">（由当日数据模板化生成，无主观臆测）</span>
            </div>
            <ul className="list-disc space-y-1.5 pl-5 text-sm leading-6 text-text-primary">
              {data.conclusions.map((c, i) => (
                <li key={i}>{c}</li>
              ))}
            </ul>
          </div>
        )}

        {!loading && !data && (
          <div className="flex flex-col items-center py-16 text-center">
            <Globe2 size={32} className="text-text-muted" />
            <p className="mt-3 text-sm text-text-secondary">市场数据加载失败（免费数据源可能临时限流），稍后点「刷新」重试</p>
          </div>
        )}

        <p className="pb-4 text-center text-[11px] text-text-muted">
          数据为收盘级快照，仅供研究参考，不构成投资建议
        </p>
      </div>
    </div>
  )
}

function Stat({ label, value, color = '', note }: { label: string; value: string; color?: string; note?: string }) {
  return (
    <div className="rounded-lg border border-border/70 bg-surface p-3">
      <div className="text-xs text-text-secondary">{label}</div>
      <div className={`num mt-1 text-xl font-semibold ${color}`}>{value}</div>
      {note && <div className="mt-0.5 text-[11px] text-text-muted">{note}</div>}
    </div>
  )
}
