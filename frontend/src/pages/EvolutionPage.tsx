import {
  CheckCircle2,
  Dna,
  Loader2,
  Play,
  RotateCcw,
} from 'lucide-react'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  evolveApi,
  strategyApi,
  type EvolveMeta,
  type EvolveSummary,
  type EvolveVersion,
  type WindowMetrics,
  type Strategy,
} from '../lib/api'
import { fmt } from '../lib/utils'
import { useToast } from '../components/Toast'

interface LogLine {
  round: number
  kept: boolean
  text: string
  valSharpe?: number
}

export default function EvolutionPage() {
  const toast = useToast()
  const [meta, setMeta] = useState<EvolveMeta | null>(null)
  const [versions, setVersions] = useState<EvolveVersion[]>([])
  const [logs, setLogs] = useState<LogLine[]>([])
  const [summary, setSummary] = useState<EvolveSummary | null>(null)
  const [panelInfo, setPanelInfo] = useState<{
    universe: string[]
    days: number
    range: string
    source?: string
  } | null>(null)
  const [status, setStatus] = useState('')
  const [running, setRunning] = useState(false)
  const [runs, setRuns] = useState<{ id: number; created_at: string; summary: EvolveSummary }[]>([])
  const [bars, setBars] = useState(250)
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [strategyId, setStrategyId] = useState<number | null>(null)
  const logEndRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const [evolveTaskId, setEvolveTaskId] = useState<string | null>(null)
  const [reconnectHint, setReconnectHint] = useState('')

  const loadRuns = useCallback(() => {
    evolveApi
      .runs()
      .then(setRuns)
      .catch(() => {})
  }, [])

  useEffect(() => {
    strategyApi.list().then((items) => {
      setStrategies(items)
      if (items.length > 0) setStrategyId(items[0].id)
    }).catch((e) => toast.error(`加载我的策略失败：${e}`))
    loadRuns()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadRuns])

  useEffect(() => {
    if (!strategyId) { setMeta(null); return }
    evolveApi.meta(strategyId).then(setMeta).catch((e) => toast.error(`分析母策略参数失败：${e}`))
  }, [strategyId, toast])

  const loadRun = async (id: number) => {
    if (running) return
    try {
      const r = await evolveApi.getRun(id)
      if (!r.summary || !r.summary.init) {
        toast.error('该记录不完整')
        return
      }
      setVersions(r.history)
      setSummary(r.summary)
      if (r.summary.strategy_id) setStrategyId(Number(r.summary.strategy_id))
      setPanelInfo({
        universe: r.universe,
        days: r.history[0]?.metrics?.train?.n_days
          ? r.history[0].metrics.train.n_days * 2
          : 0,
        range: '',
      })
      setLogs([])
      setStatus(`已载入历史记录 #${id}（${r.created_at}）`)
    } catch (e) {
      toast.error(`载入失败：${e}`)
    }
  }

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs])

  useEffect(() => {
    return () => {
      abortRef.current?.abort()
    }
  }, [])

  const run = async () => {
    if (running) return
    if (!strategyId) { toast.error('请先在策略中心保存策略，并选择一个母策略'); return }
    if (!meta || Object.keys(meta.param_space).length === 0) { toast.error('当前策略没有可进化的 g.数字参数'); return }
    abortRef.current?.abort()
    const ab = new AbortController()
    abortRef.current = ab
    setRunning(true)
    setLogs([])
    setVersions([])
    setSummary(null)
    setPanelInfo(null)
    setStatus('连接中…')
    setReconnectHint('')
    try {
      const res = await fetch('/api/evolve/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rounds: 12, seed: 42, bars, strategy_id: strategyId }),
        signal: ab.signal,
      })
      const reader = res.body!.getReader()
      const dec = new TextDecoder()
      let buf = ''
      const collected: EvolveVersion[] = []
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const parts = buf.split('\n\n')
        buf = parts.pop() || ''
        for (const part of parts) {
          const line = part.split('\n').find((l) => l.startsWith('data: '))
          if (!line) continue
          let ev: Record<string, unknown>
          try {
            ev = JSON.parse(line.slice(6))
          } catch {
            continue
          }
          switch (ev.type) {
            case 'status':
              setStatus(String(ev.text))
              break
            case 'panel':
              setPanelInfo({
                universe: ev.universe as string[],
                days: Number(ev.days),
                range: String(ev.range),
                source: String(ev.source ?? ''),
              })
              setStatus(`已加载 ${(ev.universe as string[]).length} 只股票 · ${ev.days} 个交易日`)
              break
            case 'init':
              collected.push({
                version: String(ev.version),
                config: ev.config as Record<string, number>,
                metrics: ev.metrics as EvolveVersion['metrics'],
                kept: true,
                change: '初始版本',
                parent: null,
              })
              setVersions([...collected])
              break
            case 'round': {
              const kept = Boolean(ev.kept)
              setLogs((prev) => [
                ...prev,
                {
                  round: Number(ev.round),
                  kept,
                  text: String(ev.change ?? ''),
                  valSharpe:
                    ev.val_sharpe !== undefined ? Number(ev.val_sharpe) : undefined,
                },
              ])
              break
            }
            case 'done':
              if (ev.summary) {
                setVersions(ev.history as EvolveVersion[])
                setSummary(ev.summary as EvolveSummary)
                setStatus('进化完成')
                setReconnectHint('')
              }
              break
            case 'error':
              toast.error(String(ev.text))
              setStatus(String(ev.text))
              break
          }
        }
      }
    } catch (e) {
      if ((e as Error).name !== 'AbortError') {
        toast.error(`自进化请求失败：${e}`)
        setReconnectHint('连接中断，后台可能仍在运行，可从历史记录查看结果')
      }
    } finally {
      setRunning(false)
      loadRuns()
    }
  }

  // 重连：回到页面时若之前有任务在跑，尝试刷新状态
  useEffect(() => {
    if (!running && runs.length > 0 && !summary) {
      setReconnectHint('检测到进行中的任务，可点击历史记录查看最新状态')
    }
  }, [running, runs.length, summary])

  const bestConfig = versions.length > 0 ? versions[versions.length - 1].config : null
  const chartData = versions.map((v) => ({
    version: v.version,
    train: v.metrics.train.sharpe,
    val: v.metrics.val.sharpe,
    test: v.metrics.test?.sharpe,
  }))

  return (
    <div className="flex h-full overflow-hidden">
      {/* 左：协议面板 */}
      <div className="w-[320px] shrink-0 overflow-y-auto border-r border-border bg-surface/40 p-5">
        <h3 className="text-sm font-medium">三层框架</h3>
        <div className="mt-3 space-y-2">
          <LayerRow title="策略协议层" desc="内核固定，只调有界参数，每轮只动一个" />
          <LayerRow title="版本管理层" desc="每轮独立版本，记录配置/指标/父版本" />
          <LayerRow
            title="样本隔离层"
            desc={`训练 ${Math.round((meta?.split.train ?? 0.5) * 100)}% / 验证 ${Math.round((meta?.split.val ?? 0.25) * 100)}% / 测试 ${Math.round((meta?.split.test ?? 0.25) * 100)}%`}
          />
        </div>

        <h3 className="mt-5 text-sm font-medium">母策略 · 我的策略</h3>
        {strategies.length > 0 ? (
          <select
            value={strategyId ?? ''}
            onChange={(e) => setStrategyId(Number(e.target.value))}
            disabled={running}
            className="mt-2 w-full rounded-lg border border-teal/40 bg-card px-3 py-2 text-sm outline-none"
          >
            {strategies.map((strategy) => (
              <option key={strategy.id} value={strategy.id}>{strategy.name}</option>
            ))}
          </select>
        ) : (
          <div className="mt-2 rounded-lg border border-warn/30 bg-warn/5 p-3 text-xs leading-5 text-warn">
            策略中心还没有已保存策略。请先新建并保存母策略，再回来进化。
          </div>
        )}
        {meta?.strategy && (
          <div className="mt-2 rounded-lg border border-border bg-card p-3 text-[11px] leading-5 text-text-secondary">
            <div className="font-medium text-teal">正在进化：{meta.strategy.name}</div>
            <div>{meta.strategy.description || '未填写策略说明'}</div>
            <div className="mt-1 text-text-muted">只修改 initialize 中声明的 g.数字参数，不覆盖母策略代码。</div>
          </div>
        )}

        {meta && (
          <>
            <h3 className="mt-5 text-sm font-medium">策略协议层 · 参数空间</h3>
            <div className="mt-2 space-y-1.5">
              {Object.entries(meta.param_space).map(([key, spec]) => (
                <div
                  key={key}
                  className="rounded-lg border border-border bg-card px-3 py-2 text-xs"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-teal">{key}</span>
                    <span className="num text-text-secondary">
                      {spec.min}~{spec.max} (步长{spec.step})
                    </span>
                  </div>
                  <div className="mt-0.5 text-text-muted">
                    {spec.desc} · 默认 {spec.default}
                    {bestConfig && bestConfig[key] !== spec.default && (
                      <span className="text-gain"> → {bestConfig[key]}</span>
                    )}
                  </div>
                </div>
              ))}
              {Object.keys(meta.param_space).length === 0 && (
                <div className="rounded-lg border border-warn/30 bg-warn/5 p-3 text-[11px] leading-5 text-warn">
                  未发现可调参数。请在母策略 initialize 中声明，例如 g.fast = 5、g.slow = 20。
                </div>
              )}
            </div>

            <h3 className="mt-5 text-sm font-medium">回测标的</h3>
            <div className="mt-2 max-h-40 overflow-y-auto rounded-lg border border-border bg-card p-2 text-[11px] leading-5 text-text-secondary">
              {panelInfo?.universe.length ? panelInfo.universe.join(' · ') : '自动读取母策略代码中的证券代码'}
            </div>
            {panelInfo && (
              <div className="mt-2 text-[11px] text-text-muted">
                {panelInfo.days} 个交易日 · {panelInfo.range}
                {panelInfo.source && <div>来源：{panelInfo.source}</div>}
              </div>
            )}
          </>
        )}

        <div className="mt-5 flex items-center gap-2 text-xs text-text-secondary">
          <span>回看窗口：</span>
          <select
            value={bars}
            onChange={(e) => setBars(Number(e.target.value))}
            disabled={running}
            className="num flex-1 rounded border border-border bg-card px-2 py-1.5 outline-none"
          >
            <option value={250}>250 交易日（约 1 年）</option>
            <option value={500}>500 交易日（约 2 年）</option>
            <option value={750}>750 交易日（约 3 年）</option>
          </select>
        </div>
        <button
          onClick={run}
          disabled={running || !strategyId || !meta || Object.keys(meta.param_space).length === 0}
          className="mt-2 flex w-full items-center justify-center gap-1.5 rounded-lg bg-teal py-2.5 text-sm font-medium text-white hover:bg-teal/85 disabled:opacity-50"
        >
          {running ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />}
          {running ? '进化中…' : !strategyId ? '请先保存母策略' : '启动自进化'}
        </button>
        {status && <p className="mt-2 text-center text-xs text-text-secondary">{status}</p>}

        {runs.length > 0 && (
          <>
            <h3 className="mt-5 text-sm font-medium">历史记录</h3>
            <div className="mt-2 max-h-44 space-y-1 overflow-y-auto">
              {runs.map((r) => (
                <button
                  key={r.id}
                  onClick={() => loadRun(r.id)}
                  className="w-full rounded-lg border border-border bg-card px-3 py-2 text-left text-xs hover:border-teal/40"
                >
                  <span className="num text-teal">#{r.id}</span>
                  <span className="ml-2 text-text-secondary">{r.created_at.slice(5, 16)}</span>
                  <span className="ml-2 text-text-muted">
                    {r.summary?.strategy_name || '蓝筹动量轮动'} · {r.summary?.versions_kept ?? '?'} 版本 · 测试夏普{' '}
                    {r.summary?.final?.test_sharpe ?? '—'}
                  </span>
                </button>
              ))}
            </div>
          </>
        )}
      </div>

      {/* 右：结果 */}
      <div className="min-w-0 flex-1 overflow-y-auto p-5">
        {summary ? (
          <div className="mx-auto max-w-4xl space-y-4">
            {/* Summary hero */}
            <div className="rounded-xl border border-teal/30 bg-gradient-to-br from-teal/15 to-transparent p-5">
              <div className="flex items-center gap-2 text-sm text-teal">
                <Dna size={16} />
                {summary.strategy_name || '蓝筹动量轮动'} · 跑了 {summary.rounds_run} 轮 · 保留 {summary.versions_kept} 个版本
              </div>
              <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
                <EvoStat label="测试集夏普" init={summary.init.test_sharpe} final={summary.final.test_sharpe} />
                <EvoStat label="测试集年化" init={summary.init.test_ann_return} final={summary.final.test_ann_return} suffix="%" />
                <EvoStat label="测试集回撤" init={summary.init.test_max_drawdown} final={summary.final.test_max_drawdown} suffix="%" lowerBetter />
                <EvoStat label="验证集夏普" init={summary.init.val_sharpe} final={summary.final.val_sharpe} />
              </div>
            </div>

            {/* 夏普进化曲线 */}
            {chartData.length > 1 && (
              <div className="rounded-xl border border-border bg-card p-4">
                <h3 className="mb-2 text-sm font-medium">夏普进化曲线</h3>
                <div style={{ height: 240 }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={chartData}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#1e2d47" />
                      <XAxis
                        dataKey="version"
                        tick={{ fill: '#475569', fontSize: 10 }}
                        tickLine={false}
                      />
                      <YAxis
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
                      />
                      <Legend wrapperStyle={{ fontSize: 12 }} />
                      <Line type="monotone" dataKey="train" stroke="#64748b" strokeWidth={1.5} dot={false} name="训练集" />
                      <Line type="monotone" dataKey="val" stroke="#14b8a6" strokeWidth={2} dot={{ r: 2 }} name="验证集" />
                      <Line type="monotone" dataKey="test" stroke="#f59e0b" strokeWidth={1.5} dot={{ r: 2 }} name="测试集" />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
                <p className="mt-2 text-[11px] leading-5 text-text-muted">
                  验证集一路上升、测试集纹丝不动甚至下跌 —— 那不是 bug，
                  正是「验证集改善 ≠ 真实外推能力」的过拟合真相。
                </p>
              </div>
            )}

            {/* 迭代日志 */}
            <div className="rounded-xl border border-border bg-card p-4">
              <h3 className="mb-2 text-sm font-medium">迭代日志</h3>
              <div className="max-h-64 space-y-1 overflow-y-auto font-mono text-xs">
                {logs.map((l, i) => (
                  <div key={i} className="flex items-center gap-2">
                    {l.kept ? (
                      <CheckCircle2 size={12} className="shrink-0 text-gain" />
                    ) : (
                      <RotateCcw size={12} className="shrink-0 text-text-muted" />
                    )}
                    <span className="text-text-muted">R{l.round}</span>
                    <span className={l.kept ? 'text-text-primary' : 'text-text-muted'}>
                      {l.kept ? `保留 · ${l.text}` : l.text}
                    </span>
                    {l.valSharpe !== undefined && (
                      <span className="num ml-auto shrink-0 text-teal">
                        val {l.valSharpe}
                      </span>
                    )}
                  </div>
                ))}
                {running && (
                  <div className="flex items-center gap-2 text-text-muted">
                    <Loader2 size={12} className="animate-spin" /> 运行中…
                  </div>
                )}
                <div ref={logEndRef} />
              </div>
            </div>

            {/* 版本历史 */}
            <div className="overflow-hidden rounded-xl border border-border bg-card">
              <div className="border-b border-border px-4 py-2.5 text-sm font-medium">
                版本历史（保留版本）
              </div>
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-xs text-text-secondary">
                    <th className="px-4 py-2 text-left font-normal">版本</th>
                    <th className="px-4 py-2 text-left font-normal">改动</th>
                    <th className="px-4 py-2 text-right font-normal">训练夏普</th>
                    <th className="px-4 py-2 text-right font-normal">验证夏普</th>
                    <th className="px-4 py-2 text-right font-normal">测试夏普</th>
                  </tr>
                </thead>
                <tbody>
                  {versions
                    .filter((v) => v.kept)
                    .map((v, i, arr) => (
                      <tr
                        key={v.version}
                        className={`border-b border-border/50 last:border-0 ${
                          i === arr.length - 1 ? 'bg-teal/10' : ''
                        }`}
                      >
                        <td className="num px-4 py-2 text-teal">{v.version}</td>
                        <td className="px-4 py-2 text-text-secondary">{v.change}</td>
                        <td className="num px-4 py-2 text-right text-text-muted">
                          {fmt(v.metrics.train.sharpe, 2)}
                        </td>
                        <td className="num px-4 py-2 text-right text-teal">
                          {fmt(v.metrics.val.sharpe, 2)}
                        </td>
                        <td className="num px-4 py-2 text-right text-warn">
                          {v.metrics.test ? fmt(v.metrics.test.sharpe, 2) : '封存'}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>

            <p className="pb-2 text-center text-[11px] text-text-muted">
              回测与自进化结果仅供研究演示，历史表现不代表未来收益
            </p>
          </div>
        ) : (
          <div className="flex h-full flex-col items-center justify-center text-center">
            <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-teal/40 bg-teal/15">
              <Dna size={26} className="text-teal" />
            </div>
            <h3 className="mt-4 font-medium">AutoResearch · 约束化的自动迭代</h3>
            <p className="mt-1 max-w-md text-sm leading-6 text-text-secondary">
              从“我的策略”选择母策略。系统只调整该策略声明的 g.数字参数：每轮只动一个参数，验证集夏普提升才保留版本，
              否则回滚；测试集只在最后复核一次，绝不进入调参闭环。
            </p>
            <p className="mt-3 max-w-md text-xs leading-5 text-text-muted">
              方法论：华泰金工《自进化Skill：选股策略的自动迭代》× Karpathy AutoResearch
            </p>
          </div>
        )}
      </div>
    </div>
  )
}

function LayerRow({ title, desc }: { title: string; desc: string }) {
  return (
    <div className="rounded-lg border border-border bg-card px-3 py-2">
      <div className="text-xs font-medium text-teal">{title}</div>
      <div className="mt-0.5 text-[11px] leading-4 text-text-secondary">{desc}</div>
    </div>
  )
}

function EvoStat({
  label,
  init,
  final,
  suffix = '',
  lowerBetter = false,
}: {
  label: string
  init: number
  final: number
  suffix?: string
  lowerBetter?: boolean
}) {
  const improved = lowerBetter ? final < init : final > init
  return (
    <div className="rounded-lg border border-border/60 bg-card/60 p-3">
      <div className="text-xs text-text-secondary">{label}</div>
      <div className="num mt-1 text-lg">
        <span className="text-text-muted">{fmt(init, 2)}{suffix}</span>
        <span className="mx-1 text-text-muted">→</span>
        <span className={improved ? 'text-gain' : 'text-loss'}>
          {fmt(final, 2)}{suffix}
        </span>
      </div>
    </div>
  )
}
