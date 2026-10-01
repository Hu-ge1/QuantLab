import { Activity, Bot, Check, Loader2, Play, RefreshCw, Send, SlidersHorizontal } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { bridgeApi, screenerApi, type BasketBacktestResult, type ScreenFilters, type ScreenResult } from '../lib/api'
import { useToast } from '../components/Toast'

const empty = (v: string) => (v.trim() === '' ? undefined : Number(v))
const fmt = (v: number | null | undefined, digits = 2) => v == null ? '—' : v.toFixed(digits)

export default function ScreenerPage() {
  const toast = useToast()
  const navigate = useNavigate()
  const [filters, setFilters] = useState<ScreenFilters>({ market: 'main', profile: 'momentum', exclude_st: true, above_ma60: true, strict_uptrend: true, use_financial_quality: true, min_amount_yi: 1, limit: 100 })
  const [result, setResult] = useState<ScreenResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [backtest, setBacktest] = useState<BasketBacktestResult | null>(null)
  const [backtesting, setBacktesting] = useState(false)

  const run = async (force = false) => {
    setLoading(true)
    try {
      const data = await screenerApi.run({ ...filters, force })
      setResult(data)
      setSelected(new Set(data.results.slice(0, 10).map((r) => r.code)))
      setBacktest(null)
    } catch (e) {
      toast.error(`筛选失败：${e}`)
    } finally { setLoading(false) }
  }

  const selectedRows = useMemo(() => result?.results.filter((r) => selected.has(r.code)) ?? [], [result, selected])
  const setNum = (key: keyof ScreenFilters, value: string) => setFilters((f) => ({ ...f, [key]: empty(value) }))
  const toggle = (code: string) => setSelected((old) => {
    const next = new Set(old); next.has(code) ? next.delete(code) : next.add(code); return next
  })

  const syncQmt = async () => {
    if (!selected.size) return toast.error('请先选择股票')
    try {
      const old = await bridgeApi.watchlistGet()
      const saved = await bridgeApi.watchlist([...new Set([...old.codes, ...selected])])
      toast.success(`已同步到 QMT 自选，共 ${saved.codes.length} 只`)
    } catch (e) { toast.error(`同步失败：${e}`) }
  }

  const askAi = () => {
    if (!selectedRows.length) return toast.error('请先选择股票')
    const lines = selectedRows.slice(0, 20).map((r) => `${r.code} ${r.name}（${r.trend_stage}，趋势分${fmt(r.factors.trend, 1)}，距MA60 ${fmt(r.ma60_distance_pct)}%）`).join('、')
    const prompt = `请基于真实工具数据进一步比较这些量化筛选候选：${lines}。请核验行情、估值与风险，逐项标注来源和截止日期，并说明数据缺口；不要直接给买卖指令。`
    navigate(`/chat?prompt=${encodeURIComponent(prompt)}`)
  }

  const diagnose = async () => {
    if (!selected.size) return toast.error('请先选择股票')
    setBacktesting(true)
    try { setBacktest(await screenerApi.backtest([...selected].slice(0, 20), 252)) }
    catch (e) { toast.error(`历史体检失败：${e}`) }
    finally { setBacktesting(false) }
  }

  return (
    <div className="h-full overflow-y-auto p-5">
      <div className="mx-auto max-w-[1500px] space-y-4">
        <div className="rounded-xl border border-border bg-card p-4">
          <div className="mb-3 flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-sm font-semibold"><SlidersHorizontal size={16} className="text-accent" />筛选条件</div>
            <div className="flex gap-2">
              <button onClick={() => run(true)} disabled={loading} className="flex items-center gap-1 rounded-lg border border-border px-3 py-2 text-xs text-text-secondary hover:text-text-primary disabled:opacity-50"><RefreshCw size={13} />刷新数据</button>
              <button onClick={() => run(false)} disabled={loading} className="flex items-center gap-1 rounded-lg bg-accent px-4 py-2 text-xs text-white disabled:opacity-50">{loading ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}开始扫描</button>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
            <Field label="代码/名称"><input value={filters.keyword ?? ''} onChange={(e) => setFilters({ ...filters, keyword: e.target.value })} placeholder="可留空" className="input" /></Field>
            <Field label="市场"><input value="沪深 A 股主板" disabled className="input cursor-not-allowed opacity-80" /></Field>
            <Field label="评分风格"><select value={filters.profile} onChange={(e) => setFilters({ ...filters, profile: e.target.value as ScreenFilters['profile'] })} className="input"><option value="momentum">中期趋势</option><option value="balanced">均衡</option><option value="value">价值</option></select></Field>
            <NumField label="最低市值(亿)" value={filters.min_market_cap} onChange={(v) => setNum('min_market_cap', v)} />
            <NumField label="最高PE(TTM)" value={filters.max_pe} onChange={(v) => setNum('max_pe', v)} />
            <NumField label="最高PB" value={filters.max_pb} onChange={(v) => setNum('max_pb', v)} />
            <NumField label="最低成交额(亿)" value={filters.min_amount_yi} onChange={(v) => setNum('min_amount_yi', v)} />
            <NumField label="最低换手率%" value={filters.min_turnover} onChange={(v) => setNum('min_turnover', v)} />
            <NumField label="最低涨跌幅%" value={filters.min_change} onChange={(v) => setNum('min_change', v)} />
            <NumField label="最高涨跌幅%" value={filters.max_change} onChange={(v) => setNum('max_change', v)} />
            <NumField label="最低毛利率%" value={filters.min_gross_margin} onChange={(v) => setNum('min_gross_margin', v)} />
            <NumField label="最低营收同比%" value={filters.min_revenue_yoy} onChange={(v) => setNum('min_revenue_yoy', v)} />
            <NumField label="最低净利同比%" value={filters.min_net_profit_yoy} onChange={(v) => setNum('min_net_profit_yoy', v)} />
            <NumField label="返回数量" value={filters.limit} onChange={(v) => setNum('limit', v)} />
            <label className="flex items-end gap-2 pb-2 text-xs text-text-secondary"><input type="checkbox" checked={filters.exclude_st} onChange={(e) => setFilters({ ...filters, exclude_st: e.target.checked })} />排除 ST / 退市</label>
            <label className="flex items-end gap-2 pb-2 text-xs text-text-secondary"><input type="checkbox" checked={filters.above_ma60 ?? false} onChange={(e) => setFilters({ ...filters, above_ma60: e.target.checked })} />仅保留站上 MA60</label>
            <label className="flex items-end gap-2 pb-2 text-xs text-text-secondary"><input type="checkbox" checked={filters.strict_uptrend ?? false} onChange={(e) => setFilters({ ...filters, strict_uptrend: e.target.checked })} />严格多头排列</label>
            <label className="flex items-end gap-2 pb-2 text-xs text-text-secondary"><input type="checkbox" checked={filters.use_financial_quality ?? true} onChange={(e) => setFilters({ ...filters, use_financial_quality: e.target.checked })} />FFD 财务质量/增长</label>
          </div>
        </div>

        {result && <>
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-card px-4 py-3 text-xs">
            <div><span className="text-text-primary">扫描 {result.scanned} 只，命中 {result.matched} 只</span><span className="ml-3 text-text-muted">{result.source} · {result.as_of}</span></div>
            <div className="flex gap-2"><button onClick={diagnose} disabled={backtesting} className="flex items-center gap-1 rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-warn disabled:opacity-50">{backtesting ? <Loader2 size={13} className="animate-spin" /> : <Activity size={13} />}历史体检</button><button onClick={syncQmt} className="flex items-center gap-1 rounded-lg border border-teal/40 bg-teal/10 px-3 py-2 text-teal"><Send size={13} />同步 QMT 自选 ({selected.size})</button><button onClick={askAi} className="flex items-center gap-1 rounded-lg border border-accent/40 bg-accent/10 px-3 py-2 text-accent"><Bot size={13} />交给 AI 深研</button></div>
          </div>
          <Coverage coverage={result.coverage} />
          {backtest && <BacktestPanel data={backtest} />}
          <div className="overflow-hidden rounded-xl border border-border bg-card">
            <div className="overflow-x-auto"><table className="w-full min-w-[1650px] text-xs">
              <thead className="bg-surface text-text-muted"><tr><Th>选</Th><Th>排名</Th><Th>代码</Th><Th>名称</Th><Th>趋势阶段</Th><Th right>综合分</Th><Th right>现价</Th><Th right>涨跌%</Th><Th right>成交额(亿)</Th><Th right>换手%</Th><Th right>PE</Th><Th right>PB</Th><Th right>市值(亿)</Th><Th right>毛利率%</Th><Th right>营收同比%</Th><Th right>净利同比%</Th><Th right>距MA60%</Th><Th right>MA60/120差%</Th><Th right>MA120/250差%</Th><Th right>价值</Th><Th right>质量</Th><Th right>增长</Th><Th right>趋势</Th><Th right>短强</Th><Th right>流动性</Th><Th right>稳定性</Th></tr></thead>
              <tbody>{result.results.map((r, i) => <tr key={r.code} className="border-t border-border/60 hover:bg-surface/70" onClick={() => toggle(r.code)}>
                <td className="px-3 py-2"><span className={`flex h-4 w-4 items-center justify-center rounded border ${selected.has(r.code) ? 'border-accent bg-accent text-white' : 'border-border'}`}>{selected.has(r.code) && <Check size={11} />}</span></td>
                <td className="px-3 py-2 text-text-muted">{i + 1}</td><td className="num px-3 py-2 text-teal">{r.code}</td><td className="px-3 py-2 text-text-primary">{r.name}</td><td className="whitespace-nowrap px-3 py-2 text-accent">{r.trend_stage}</td>
                <Td v={r.score} strong /><Td v={r.price} /><td className={`num px-3 py-2 text-right ${(r.change_pct ?? 0) >= 0 ? 'text-gain' : 'text-loss'}`}>{fmt(r.change_pct)}</td><Td v={r.amount_yi} /><Td v={r.turnover} /><Td v={r.pe_ttm} /><Td v={r.pb} /><Td v={r.market_cap_yi} /><Td v={r.gross_margin} /><Td v={r.revenue_yoy} /><Td v={r.net_profit_yoy} /><Td v={r.ma60_distance_pct} /><Td v={r.ma60_120_spread_pct} /><Td v={r.ma120_250_spread_pct} /><Td v={r.factors.value} /><Td v={r.factors.quality} /><Td v={r.factors.growth} /><Td v={r.factors.trend} /><Td v={r.factors.strength} /><Td v={r.factors.liquidity} /><Td v={r.factors.stability} />
              </tr>)}</tbody>
            </table></div>
            <div className="border-t border-border px-4 py-3 text-[11px] leading-5 text-text-muted">{result.factor_note}<br />{result.warnings.join('；')}</div>
          </div>
        </>}
        {!result && !loading && <div className="rounded-xl border border-dashed border-border p-12 text-center text-sm text-text-muted">设置条件后开始扫描。首次全市场取数可能需要几十秒，之后使用 5 分钟缓存。</div>}
      </div>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) { return <label className="text-[11px] text-text-muted">{label}{children}</label> }
function NumField({ label, value, onChange }: { label: string; value?: number; onChange: (v: string) => void }) { return <Field label={label}><input type="number" value={value ?? ''} onChange={(e) => onChange(e.target.value)} className="input" /></Field> }
function Th({ children, right = false }: { children: React.ReactNode; right?: boolean }) { return <th className={`px-3 py-2.5 font-medium ${right ? 'text-right' : 'text-left'}`}>{children}</th> }
function Td({ v, strong = false }: { v: number | null | undefined; strong?: boolean }) { return <td className={`num px-3 py-2 text-right ${strong ? 'font-semibold text-accent' : 'text-text-secondary'}`}>{fmt(v, 1)}</td> }

function Coverage({ coverage }: { coverage: ScreenResult['coverage'] }) {
  const labels: Record<string, string> = { price: '价格', pe_ttm: 'PE', pb: 'PB', market_cap_yi: '市值', ma60: 'MA60', ma120: 'MA120', ma250: 'MA250', volume_ratio: '量比', gross_margin: '毛利率', revenue_yoy: '营收同比', net_profit_yoy: '净利同比' }
  return <div className="grid grid-cols-4 gap-2 md:grid-cols-8 xl:grid-cols-11">{Object.entries(coverage).map(([key, item]) => <div key={key} className="rounded-lg border border-border bg-card px-3 py-2"><div className="text-[10px] text-text-muted">{labels[key] ?? key}</div><div className={`num mt-1 text-sm ${item.rate >= 90 ? 'text-gain' : item.rate >= 70 ? 'text-warn' : 'text-loss'}`}>{item.rate}%</div><div className="text-[9px] text-text-muted">{item.valid}/{item.total}</div></div>)}</div>
}

function BacktestPanel({ data }: { data: BasketBacktestResult }) {
  const m = data.metrics
  return <div className="rounded-xl border border-warn/30 bg-card p-4">
    <div className="flex flex-wrap items-center justify-between gap-2"><div className="text-sm font-semibold">候选组合历史体检</div><div className="text-xs text-text-muted">{data.start_date} ～ {data.end_date} · {data.trading_days}日 · 有效覆盖 {data.coverage_rate}%</div></div>
    <div className="mt-3 grid grid-cols-3 gap-2 md:grid-cols-6"><Metric label="区间收益" value={m.total_return} suffix="%" /><Metric label="年化收益" value={m.annualized_return} suffix="%" /><Metric label="年化波动" value={m.annualized_volatility} suffix="%" /><Metric label="最大回撤" value={m.max_drawdown} suffix="%" /><Metric label="夏普" value={m.sharpe} /><Metric label="上涨日占比" value={m.positive_day_ratio} suffix="%" /></div>
    <div className="mt-4 h-52"><ResponsiveContainer width="100%" height="100%"><LineChart data={data.curve}><XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={35} /><YAxis tick={{ fontSize: 10 }} domain={['auto', 'auto']} /><Tooltip contentStyle={{ background: '#111e33', border: '1px solid #1e2d47', fontSize: 11 }} /><Legend /><Line type="monotone" dataKey="basket" name="候选等权" stroke="#3b82f6" dot={false} strokeWidth={2} /><Line type="monotone" dataKey="benchmark" name="沪深300" stroke="#14b8a6" dot={false} /></LineChart></ResponsiveContainer></div>
    <div className="mt-3 rounded-lg border border-warn/30 bg-warn/5 px-3 py-2 text-[11px] leading-5 text-warn">{data.warning}</div>
    {data.skipped.length > 0 && <div className="mt-2 text-[11px] text-loss">已排除：{data.skipped.map((x) => `${x.code}（${x.reason}）`).join('；')}</div>}
  </div>
}

function Metric({ label, value, suffix = '' }: { label: string; value: number | null | undefined; suffix?: string }) { return <div className="rounded-lg bg-surface px-3 py-2"><div className="text-[10px] text-text-muted">{label}</div><div className="num mt-1 text-sm text-text-primary">{value == null ? '—' : `${value}${suffix}`}</div></div> }
