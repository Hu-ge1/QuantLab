import Editor from '@monaco-editor/react'
import { Loader2, Pencil, Play, Plus, Radio, Trash2 } from 'lucide-react'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  strategyApi,
  type BacktestResult,
  type BacktestStats,
  type Strategy,
} from '../lib/api'
import { fmt, fmtPct, pnlColor } from '../lib/utils'
import { useToast } from '../components/Toast'

const STRATEGY_TEMPLATES: { name: string; desc: string; code: string }[] = [
  {
    name: '双均线择时',
    desc: '快线上穿慢线买入、下穿卖出（默认 5/20，可改参数）',
    code: `def initialize(context):
    g.security = "510300.SH"          # 标的：沪深300 ETF
    g.fast, g.slow = 5, 20
    set_benchmark("000300.SH")

def handle_data(context, data):
    f = attribute_history(g.security, g.fast, "1d", ["close"])
    s = attribute_history(g.security, g.slow, "1d", ["close"])
    if len(s) < g.slow:
        return
    fast = sum(b["close"] for b in f) / g.fast
    slow = sum(b["close"] for b in s) / g.slow
    price = get_price(g.security, count=1)[0]["close"]
    pos = context.portfolio.positions.get(g.security)
    held = pos.amount if pos else 0
    if fast > slow and held == 0:
        order_value(g.security, context.portfolio.cash * 0.95)
    elif fast < slow and held > 0:
        order_target(g.security, 0)
`,
  },
  {
    name: '买入持有',
    desc: '第一天满仓买入后不再操作，作为基准对照',
    code: `def initialize(context):
    g.security = "510300.SH"
    set_benchmark("000300.SH")

def handle_data(context, data):
    pos = context.portfolio.positions.get(g.security)
    if not pos or pos.amount == 0:
        order_value(g.security, context.portfolio.cash)
`,
  },
  {
    name: 'ETF 动量轮动 Top2',
    desc: '5 只 ETF 按 20 日动量排名，持有最强的 2 只，每 10 日调仓',
    code: `def initialize(context):
    g.pool = ["510300.SH", "512100.SH", "518880.SH", "513100.SH", "159915.SZ"]
    g.top_n = 2
    g.rebalance = 10
    g.day = 0
    set_benchmark("000300.SH")

def handle_data(context, data):
    g.day += 1
    if g.day % g.rebalance != 1:
        return
    scores = []
    for sec in g.pool:
        bars = attribute_history(sec, 20, "1d", ["close"])
        if len(bars) < 20:
            continue
        scores.append((sec, bars[-1]["close"] / bars[0]["close"] - 1))
    scores.sort(key=lambda x: x[1], reverse=True)
    picks = [s for s, m in scores[:g.top_n] if m > 0]
    for sec in list(context.portfolio.positions.keys()):
        if sec not in picks:
            order_target(sec, 0)
    for sec in picks:
        if sec not in context.portfolio.positions:
            order_value(sec, context.portfolio.cash / max(1, len(picks)))
`,
  },
  {
    name: '简易网格',
    desc: '以首日价格为锚，跌 3% 买一格、涨 3% 清一次（可改步长）',
    code: `def initialize(context):
    g.security = "510300.SH"
    g.base = None
    g.step = 0.03
    g.per = 0.3        # 每格用 30% 初始资金
    set_benchmark("000300.SH")

def handle_data(context, data):
    price = get_price(g.security, count=1)[0]["close"]
    pos = context.portfolio.positions.get(g.security)
    held = pos.amount if pos else 0
    if g.base is None:
        g.base = price
        return
    if price <= g.base * (1 - g.step) and held == 0:
        order_value(g.security, context.portfolio.starting_cash * g.per)
        g.base = price
    elif held > 0 and price >= g.base * (1 + g.step):
        order_target(g.security, 0)
        g.base = price
`,
  },
]

const DEFAULT_CODE = STRATEGY_TEMPLATES[0].code

function oneYearAgo(): string {
  const d = new Date()
  d.setFullYear(d.getFullYear() - 1)
  return d.toISOString().slice(0, 10)
}

function yesterday(): string {
  const d = new Date()
  d.setDate(d.getDate() - 1)
  return d.toISOString().slice(0, 10)
}

export default function StrategyPage() {
  const toast = useToast()
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [selected, setSelected] = useState<Strategy | null>(null)
  const [code, setCode] = useState(DEFAULT_CODE)
  const [tab, setTab] = useState<'code' | 'result'>('code')
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<BacktestResult | null>(null)
  const [showNew, setShowNew] = useState(false)
  const [newName, setNewName] = useState('')
  const [tplIdx, setTplIdx] = useState(0)
  const [btHistory, setBtHistory] = useState<{ id: number; created_at: string; stats: BacktestStats }[]>([])
  const [currentResultId, setCurrentResultId] = useState<number | null>(null)
  const [params, setParams] = useState({
    start_date: oneYearAgo(),
    end_date: yesterday(),
    initial_capital: 100000,
    benchmark: '000300.SH',
  })
  const saveTimer = useRef<number | null>(null)
  const [btTaskId, setBtTaskId] = useState<string | null>(null)
  const [btTaskStatus, setBtTaskStatus] = useState<string>('')

  const loadHistory = useCallback(async (id: number) => {
    try {
      const h = await strategyApi.backtestHistory(id)
      setBtHistory(h)
      setCurrentResultId(h[0]?.id ?? null)
    } catch {
      setBtHistory([])
    }
  }, [])

  const load = useCallback(async () => {
    try {
      const list = await strategyApi.list()
      setStrategies(list)
      if (list.length > 0 && !selected) select(list[0])
    } catch (e) {
      toast.error(`加载策略失败：${e}`)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    if (selected) loadHistory(selected.id)
  }, [selected, loadHistory])

  // 代码自动保存（1s 防抖）
  useEffect(() => {
    if (!selected) return
    if (saveTimer.current) window.clearTimeout(saveTimer.current)
    saveTimer.current = window.setTimeout(() => {
      strategyApi
        .update(selected.id, { code })
        .catch(() => {})
    }, 1000)
    return () => {
      if (saveTimer.current) window.clearTimeout(saveTimer.current)
    }
  }, [code, selected])

  const select = (s: Strategy) => {
    setSelected(s)
    setCode(s.code || DEFAULT_CODE)
    setResult(null)
    setTab('code')
  }

  const create = async () => {
    if (!newName.trim()) return toast.error('请填写策略名称')
    try {
      const code = STRATEGY_TEMPLATES[tplIdx]?.code ?? DEFAULT_CODE
      const { id } = await strategyApi.create({ name: newName.trim(), code })
      setShowNew(false)
      setNewName('')
      toast.success('策略已创建')
      const s = await strategyApi.get(id)
      setStrategies((prev) => [s, ...prev])
      select(s)
    } catch (e) {
      toast.error(`创建失败：${e}`)
    }
  }

  const remove = async (s: Strategy) => {
    if (!window.confirm(`确认删除策略「${s.name}」？`)) return
    await strategyApi.remove(s.id).catch((e) => toast.error(`删除失败：${e}`))
    if (selected?.id === s.id) setSelected(null)
    toast.success('已删除')
    load()
  }

  const toggleLive = async () => {
    if (!selected) return
    try {
      const { status } = await strategyApi.toggleLive(selected.id)
      setSelected({ ...selected, status: status as Strategy['status'] })
      load()
    } catch (e) {
      toast.error(`操作失败：${e}`)
    }
  }

  const runBacktest = async () => {
    if (!selected) return
    setRunning(true)
    try {
      await strategyApi.update(selected.id, { code })
      const { result: r } = await strategyApi.backtest(selected.id, params)
      setResult(r)
      setTab('result')
      loadHistory(selected.id)
    } catch (e) {
      toast.error(`回测失败：${e}`)
      setTab('code')
    } finally {
      setRunning(false)
    }
  }

  const runBacktestAsync = async () => {
    if (!selected) return
    setRunning(true)
    setBtTaskId(null)
    setBtTaskStatus('')
    try {
      await strategyApi.update(selected.id, { code })
      const res = await strategyApi.backtestAsync(selected.id, params)
      setBtTaskId(res.task_id)
      setBtTaskStatus(res.status)
      setTab('result')
    } catch (e) {
      toast.error(`启动回测失败：${e}`)
    } finally {
      setRunning(false)
    }
  }

  // 轮询异步回测状态
  useEffect(() => {
    if (!btTaskId) return
    let timer: number
    const poll = async () => {
      try {
        const t = await strategyApi.backtestTask(btTaskId)
        setBtTaskStatus(t.status)
        if (t.status === 'done' && t.result) {
          setResult(t.result)
          setBtTaskId(null)
          loadHistory(selected!.id)
        } else if (t.status === 'error') {
          toast.error(`回测失败：${t.error || '未知错误'}`)
          setBtTaskId(null)
        } else {
          timer = window.setTimeout(poll, 1500)
        }
      } catch {
        timer = window.setTimeout(poll, 2000)
      }
    }
    poll()
    return () => {
      if (timer) window.clearTimeout(timer)
    }
  }, [btTaskId, selected, toast])

  const loadHistoryResult = async (resultId: number) => {
    if (!selected) return
    try {
      const d = await strategyApi.backtestDetail(selected.id, resultId)
      setResult(d.result)
      setCurrentResultId(resultId)
      setTab('result')
      if (d.params?.benchmark) {
        setParams((p) => ({ ...p, benchmark: d.params.benchmark }))
      }
    } catch (e) {
      toast.error(`加载历史回测失败：${e}`)
    }
  }

  return (
    <div className="flex h-full overflow-hidden">
      {/* 策略列表 */}
      <div className="hidden w-60 shrink-0 flex-col border-r border-border bg-surface/40 md:flex">
        <div className="p-3">
          <button
            onClick={() => setShowNew(true)}
            className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-teal/40 bg-teal/15 py-2 text-sm text-teal hover:bg-teal/25"
          >
            <Plus size={15} /> 新建策略
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
          {strategies.map((s) => (
            <div
              key={s.id}
              onClick={() => select(s)}
              className={`group mb-1 flex cursor-pointer items-center justify-between rounded-lg px-3 py-2 text-sm ${
                selected?.id === s.id
                  ? 'border-r-2 border-teal bg-card text-text-primary'
                  : 'text-text-secondary hover:bg-card'
              }`}
            >
              <div className="min-w-0">
                <div className="flex items-center gap-1.5 truncate">
                  {s.status === 'live' && <Radio size={11} className="shrink-0 text-gain" />}
                  <span className="truncate">{s.name}</span>
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-1.5 opacity-0 group-hover:opacity-100">
                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    const name = window.prompt('重命名策略', s.name)
                    if (name && name.trim()) {
                      strategyApi
                        .update(s.id, { name: name.trim() })
                        .then(() => {
                          toast.success('已重命名')
                          load()
                        })
                    }
                  }}
                  className="text-text-muted hover:text-accent"
                >
                  <Pencil size={13} />
                </button>
                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    remove(s)
                  }}
                  className="text-text-muted hover:text-loss"
                >
                  <Trash2 size={13} />
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* 主区 */}
      <div className="flex min-w-0 flex-1 flex-col">
        {selected ? (
          <>
            <div className="flex items-center justify-between border-b border-border bg-surface/40 px-4 py-2.5">
              <div className="flex items-center gap-3">
                <h2 className="text-sm font-medium">{selected.name}</h2>
                <div className="flex rounded-lg border border-border p-0.5 text-xs">
                  <button
                    onClick={() => setTab('code')}
                    className={`rounded px-3 py-1 ${tab === 'code' ? 'bg-card text-text-primary' : 'text-text-secondary'}`}
                  >
                    代码
                  </button>
                  <button
                    onClick={() => setTab('result')}
                    className={`rounded px-3 py-1 ${tab === 'result' ? 'bg-card text-text-primary' : 'text-text-secondary'}`}
                  >
                    回测结果
                  </button>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={toggleLive}
                  className={`rounded-lg border px-3 py-1.5 text-xs ${
                    selected.status === 'live'
                      ? 'border-gain/40 bg-gain/15 text-gain'
                      : 'border-border text-text-secondary hover:text-text-primary'
                  }`}
                >
                  {selected.status === 'live' ? '● 实盘跟踪中' : '设为实盘跟踪'}
                </button>
                <button
                  onClick={runBacktest}
                  disabled={running}
                  className="flex items-center gap-1.5 rounded-lg bg-teal px-4 py-1.5 text-sm font-medium text-white hover:bg-teal/85 disabled:opacity-50"
                >
                  {running ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : (
                    <Play size={14} />
                  )}
                  {running ? '回测中…' : '运行回测'}
                </button>
                <button
                  onClick={runBacktestAsync}
                  disabled={running}
                  className="flex items-center gap-1.5 rounded-lg border border-teal/40 bg-teal/15 px-3 py-1.5 text-sm text-teal hover:bg-teal/25 disabled:opacity-50"
                >
                  {btTaskStatus === 'running' ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : (
                    <Play size={14} />
                  )}
                  {btTaskStatus === 'running' ? '后台回测中…' : '后台回测（可切页）'}
                </button>
              </div>
              {btTaskStatus === 'running' && (
                <p className="mt-2 text-xs text-text-muted">
                  回测正在后台运行，可切换到其他页面，完成后会自动加载结果
                </p>
              )}
            </div>

            {tab === 'code' ? (
              <div className="min-h-0 flex-1">
                <Editor
                  height="100%"
                  language="python"
                  theme="vs-dark"
                  value={code}
                  onChange={(v: string | undefined) => setCode(v || '')}
                  options={{
                    fontSize: 13,
                    fontFamily: '"JetBrains Mono", "Fira Code", monospace',
                    minimap: { enabled: false },
                    scrollBeyondLastLine: false,
                    padding: { top: 16, bottom: 16 },
                    lineNumbers: 'on',
                    renderLineHighlight: 'gutter',
                    wordWrap: 'on',
                    tabSize: 4,
                  }}
                />
              </div>
            ) : (
              <div className="min-h-0 flex-1 overflow-y-auto p-5">
                {result ? (
                  <BacktestResultView r={result} />
                ) : (
                  <div className="flex h-full items-center justify-center text-sm text-text-secondary">
                    点击「运行回测」查看结果
                  </div>
                )}
              </div>
            )}

            {/* 历史回测记录（点击加载完整结果） */}
            {btHistory.length > 0 && (
              <div className="flex flex-wrap items-center gap-2 border-t border-border bg-surface/40 px-4 py-2 text-xs">
                <span className="text-text-muted">历史回测（点击查看）：</span>
                {btHistory.slice(0, 6).map((h) => (
                  <button
                    key={h.id}
                    onClick={() => loadHistoryResult(h.id)}
                    className={`num rounded border px-2 py-1 transition ${
                      h.id === currentResultId
                        ? 'border-teal/50 bg-teal/15 text-teal'
                        : 'border-border bg-card text-text-secondary hover:border-teal/40 hover:text-text-primary'
                    }`}
                    title={h.created_at}
                  >
                    {h.created_at.slice(5, 16)} ·{' '}
                    <span className={pnlColor(h.stats?.total_return ?? 0)}>
                      {fmtPct(h.stats?.total_return ?? 0)}
                    </span>
                  </button>
                ))}
              </div>
            )}

            {/* 回测参数条 */}
            <div className="flex flex-wrap items-center gap-3 border-t border-border bg-surface/40 px-4 py-3 text-xs">
              <label className="flex items-center gap-1.5 text-text-secondary">
                开始
                <input
                  type="date"
                  value={params.start_date}
                  onChange={(e) => setParams((p) => ({ ...p, start_date: e.target.value }))}
                  className="num rounded border border-border bg-card px-2 py-1.5 text-xs outline-none"
                />
              </label>
              <label className="flex items-center gap-1.5 text-text-secondary">
                结束
                <input
                  type="date"
                  value={params.end_date}
                  onChange={(e) => setParams((p) => ({ ...p, end_date: e.target.value }))}
                  className="num rounded border border-border bg-card px-2 py-1.5 text-xs outline-none"
                />
              </label>
              <label className="flex items-center gap-1.5 text-text-secondary">
                初始资金
                <input
                  type="number"
                  value={params.initial_capital}
                  step={10000}
                  onChange={(e) =>
                    setParams((p) => ({ ...p, initial_capital: Number(e.target.value) }))
                  }
                  className="num w-28 rounded border border-border bg-card px-2 py-1.5 text-xs outline-none"
                />
              </label>
              <label className="flex items-center gap-1.5 text-text-secondary">
                基准
                <input
                  value={params.benchmark}
                  onChange={(e) => setParams((p) => ({ ...p, benchmark: e.target.value }))}
                  className="num w-24 rounded border border-border bg-card px-2 py-1.5 text-xs outline-none"
                />
              </label>
            </div>
          </>
        ) : (
          <div className="flex h-full flex-col items-center justify-center text-center">
            <h3 className="font-medium">先在左侧选择或新建一个策略</h3>
            <p className="mt-1 max-w-md text-sm text-text-secondary">
              浏览器里写聚宽语法 Python，服务端沙箱执行回测，输出净值曲线、夏普与最大回撤
            </p>
          </div>
        )}
      </div>

      {/* 新建模态（带模板选择） */}
      {showNew && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="w-[560px] rounded-xl border border-border bg-card p-5 animate-slide-up">
            <h3 className="text-sm font-medium">新建策略（选择模板）</h3>
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="策略名称，如 平安MA20择时"
              autoFocus
              className="mt-3 w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-teal/50"
            />
            <div className="mt-3 max-h-64 space-y-1.5 overflow-y-auto">
              {STRATEGY_TEMPLATES.map((t, i) => (
                <button
                  key={i}
                  onClick={() => {
                    setNewName(t.name)
                    setTplIdx(i)
                  }}
                  className={`w-full rounded-lg border px-3 py-2 text-left text-xs ${
                    newName === t.name ? 'border-teal/50 bg-teal/15' : 'border-border bg-surface hover:border-teal/40'
                  }`}
                >
                  <div className="font-medium text-text-primary">{t.name}</div>
                  <div className="mt-0.5 text-[11px] text-text-muted">{t.desc}</div>
                </button>
              ))}
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => setShowNew(false)}
                className="rounded-lg border border-border px-4 py-2 text-sm text-text-secondary"
              >
                取消
              </button>
              <button
                onClick={create}
                className="rounded-lg bg-teal px-4 py-2 text-sm text-white hover:bg-teal/85"
              >
                创建
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function StatCard({ label, value, color = '' }: { label: string; value: string; color?: string }) {
  return (
    <div className="rounded-lg border border-border bg-surface p-3">
      <div className="text-xs text-text-secondary">{label}</div>
      <div className={`num mt-1 text-lg font-semibold ${color}`}>{value}</div>
    </div>
  )
}

function BacktestResultView({ r }: { r: BacktestResult }) {
  const s = r.stats
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard label="策略总收益" value={fmtPct(s.total_return)} color={pnlColor(s.total_return)} />
        <StatCard label="年化收益" value={fmtPct(s.ann_return)} color={pnlColor(s.ann_return)} />
        <StatCard
          label="基准收益"
          value={fmtPct(s.benchmark_return)}
          color={pnlColor(s.benchmark_return)}
        />
        <StatCard
          label="超额收益"
          value={fmtPct(s.total_return - s.benchmark_return)}
          color={pnlColor(s.total_return - s.benchmark_return)}
        />
        <StatCard
          label="夏普比率"
          value={fmt(s.sharpe, 2)}
          color={s.sharpe > 1 ? 'text-gain' : s.sharpe < 0 ? 'text-loss' : 'text-warn'}
        />
        <StatCard label="最大回撤" value={`-${fmt(s.max_drawdown)}%`} color="text-loss" />
        <StatCard label="最终净值" value={`¥${fmt(s.final_value, 0)}`} />
        <StatCard label="交易天数" value={String(s.trade_days)} />
      </div>

      <div className="rounded-xl border border-border bg-card p-4">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-sm font-medium">净值曲线（归一化）</h3>
          <span className="text-[11px] text-text-muted">数据源：{r.source}</span>
        </div>
        <div style={{ height: 280 }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={r.norm_curve}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e2d47" />
              <XAxis
                dataKey="date"
                tickFormatter={(d: string) => d.slice(5)}
                tick={{ fill: '#475569', fontSize: 10 }}
                tickLine={false}
                minTickGap={40}
              />
              <YAxis
                tickFormatter={(v: number) => `${((v - 1) * 100).toFixed(0)}%`}
                tick={{ fill: '#475569', fontSize: 10 }}
                tickLine={false}
                domain={['auto', 'auto']}
              />
              <Tooltip
                contentStyle={{
                  background: '#111e33',
                  border: '1px solid #1e2d47',
                  borderRadius: 8,
                  fontSize: 12,
                }}
                formatter={(v: number, name: string) => [
                  `${((v - 1) * 100).toFixed(2)}%`,
                  name === 'strategy' ? '策略' : '基准',
                ]}
              />
              <Legend
                formatter={(v: string) => (v === 'strategy' ? '策略' : '基准')}
                wrapperStyle={{ fontSize: 12 }}
              />
              <ReferenceLine y={1} stroke="#475569" strokeDasharray="4 4" />
              <Line type="monotone" dataKey="strategy" stroke="#14b8a6" strokeWidth={2} dot={false} />
              <Line
                type="monotone"
                dataKey="benchmark"
                stroke="#3b82f6"
                strokeWidth={1.5}
                strokeDasharray="4 4"
                dot={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="overflow-hidden rounded-xl border border-border bg-card">
        <div className="border-b border-border px-4 py-2.5 text-sm font-medium">资产曲线</div>
        <div className="max-h-56 overflow-y-auto">
          <table className="w-full text-sm">
            <tbody>
              {r.curve
                .filter((_, i) => i % 10 === 0 || i === r.curve.length - 1)
                .map((c) => {
                  const pct = (c.value / s.initial_value - 1) * 100
                  return (
                    <tr key={c.date} className="border-b border-border/50 last:border-0">
                      <td className="num px-4 py-2 text-text-secondary">{c.date}</td>
                      <td className="num px-4 py-2 text-right">¥{fmt(c.value, 0)}</td>
                      <td className={`num px-4 py-2 text-right ${pnlColor(pct)}`}>
                        {fmtPct(pct)}
                      </td>
                    </tr>
                  )
                })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
