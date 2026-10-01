import {
  Download,
  Loader2,
  Pencil,
  Plug,
  Plus,
  RefreshCw,
  Trash2,
  TrendingUp,
  Wallet,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  portfolioApi,
  qmtApi,
  quoteApi,
  type Position,
  type PortfolioSummary,
  type QmtAccount,
  type SecurityInfo,
} from '../lib/api'
import { fmt, fmtPct, pnlColor } from '../lib/utils'
import { useToast } from '../components/Toast'

export default function PortfolioPage() {
  const toast = useToast()
  const [positions, setPositions] = useState<Position[]>([])
  const [summary, setSummary] = useState<PortfolioSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [showForm, setShowForm] = useState(false)
  const [editing, setEditing] = useState<number | null>(null)
  const [form, setForm] = useState({ code: '', name: '', cost: '', shares: '' })
  const [searchQ, setSearchQ] = useState('')
  const [hints, setHints] = useState<SecurityInfo[]>([])
  const [qmt, setQmt] = useState<QmtAccount | null>(null)
  const [qmtLoading, setQmtLoading] = useState(false)
  const [importing, setImporting] = useState(false)
  const errorRef = useRef<(m: string) => void>(() => {})

  errorRef.current = (m: string) => toast.error(m)

  const loadQmt = useCallback(async () => {
    setQmtLoading(true)
    try {
      setQmt(await qmtApi.account())
    } catch {
      setQmt(null)
    } finally {
      setQmtLoading(false)
    }
  }, [])

  const importQmt = async () => {
    setImporting(true)
    try {
      const r = await qmtApi.importPositions(true)
      if (r.ok) {
        toast.success(`已从 QMT 导入 ${r.imported ?? 0} 条持仓（跳过 ${r.skipped ?? 0} 条已有）`)
        load()
      } else {
        toast.error(r.error || '导入失败')
      }
    } catch (e) {
      toast.error(`导入失败：${e}`)
    } finally {
      setImporting(false)
    }
  }

  const load = useCallback(async () => {
    try {
      const [list, sum] = await Promise.all([portfolioApi.list(), portfolioApi.summary()])
      setPositions(list)
      setSummary(sum)
    } catch (e) {
      errorRef.current(`加载持仓失败：${e}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    loadQmt()
  }, [load, loadQmt])

  // 代码联想（300ms 防抖）
  useEffect(() => {
    if (searchQ.trim().length < 2) {
      setHints([])
      return
    }
    const t = setTimeout(async () => {
      try {
        setHints((await quoteApi.search(searchQ.trim())).slice(0, 8))
      } catch {
        setHints([])
      }
    }, 300)
    return () => clearTimeout(t)
  }, [searchQ])

  const refreshPrices = async () => {
    setRefreshing(true)
    try {
      const r = await portfolioApi.refreshPrices()
      if (r.errors.length > 0) {
        toast.info(`价格已更新 ${r.updated} 条，${r.errors.length} 条失败`)
      } else {
        toast.success(`价格已更新 ${r.updated} 条`)
      }
      load()
    } catch (e) {
      toast.error(`刷新失败：${e}`)
    } finally {
      setRefreshing(false)
    }
  }

  const openAdd = () => {
    setEditing(null)
    setForm({ code: '', name: '', cost: '', shares: '' })
    setSearchQ('')
    setShowForm(true)
  }

  const openEdit = (p: Position) => {
    setEditing(p.id)
    setForm({
      code: p.code,
      name: p.name,
      cost: String(p.cost ?? ''),
      shares: String(p.shares ?? ''),
    })
    setSearchQ(p.code)
    setShowForm(true)
  }

  const submit = async () => {
    const cost = Number(form.cost)
    const shares = Number(form.shares)
    if (!form.code.trim()) return toast.error('请填写证券代码')
    if (!cost || cost <= 0) return toast.error('请填写有效的成本价')
    if (!shares || shares <= 0) return toast.error('请填写有效的持股数')
    try {
      const payload = {
        code: form.code.trim().toUpperCase(),
        name: form.name.trim(),
        cost,
        shares,
      }
      if (editing) {
        await portfolioApi.update(editing, payload)
        toast.success('持仓已更新')
      } else {
        await portfolioApi.add(payload)
        toast.success('持仓已添加')
      }
      setShowForm(false)
      load()
    } catch (e) {
      toast.error(`保存失败：${e}`)
    }
  }

  const remove = async (p: Position) => {
    if (!window.confirm(`确认删除持仓 ${p.name || p.code}？`)) return
    await portfolioApi.remove(p.id).catch((e) => toast.error(`删除失败：${e}`))
    toast.success('已删除')
    load()
  }

  return (
    <div className="h-full overflow-y-auto p-5">
      <div className="mx-auto max-w-5xl space-y-4">
        {/* 汇总卡 */}
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <SummaryCard
            icon={<Wallet size={15} className="text-accent" />}
            label="总成本"
            value={`¥${fmt(summary?.total_cost ?? 0)}`}
          />
          <SummaryCard
            icon={<TrendingUp size={15} className="text-teal" />}
            label="当前市值"
            value={`¥${fmt(summary?.total_value ?? 0)}`}
            note={summary?.has_live_prices ? undefined : '（按成本估算）'}
          />
          <SummaryCard
            icon={<TrendingUp size={15} className="text-teal" />}
            label="总盈亏"
            value={fmt(summary?.pnl ?? 0)}
            valueClass={pnlColor(summary?.pnl ?? 0)}
          />
          <SummaryCard
            icon={<TrendingUp size={15} className="text-teal" />}
            label="盈亏比例"
            value={fmtPct(summary?.pnl_pct ?? 0)}
            valueClass={pnlColor(summary?.pnl_pct ?? 0)}
            note={`共 ${summary?.position_count ?? 0} 只`}
          />
        </div>

        {/* QMT 实时资产面板（只读） */}
        <div className="rounded-xl border border-teal/25 bg-card p-4">
          <div className="mb-3 flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm font-medium">
              <Plug size={15} className="text-teal" />
              QMT 实时账户
              <span className="rounded border border-teal/40 bg-teal/10 px-1.5 py-0.5 text-[10px] text-teal">
                只读
              </span>
              {qmt?.online && (
                <span className="text-[11px] font-normal text-text-muted">
                  {qmt.source}
                  {qmt.last_seen ? ` · 更新于 ${String(qmt.last_seen).slice(11, 19)}` : ''}
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={loadQmt}
                disabled={qmtLoading}
                className="flex items-center gap-1 rounded-lg border border-teal/40 bg-teal/10 px-2.5 py-1.5 text-xs text-teal hover:bg-teal/20 disabled:opacity-50"
              >
                <RefreshCw size={12} className={qmtLoading ? 'animate-spin' : ''} />
                刷新
              </button>
              {qmt?.online && (qmt.positions?.length ?? 0) > 0 && (
                <button
                  onClick={importQmt}
                  disabled={importing}
                  className="flex items-center gap-1 rounded-lg border border-accent/40 bg-accent/15 px-2.5 py-1.5 text-xs text-accent hover:bg-accent/25 disabled:opacity-50"
                >
                  {importing ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />}
                  同步到持仓管理
                </button>
              )}
            </div>
          </div>

          {qmtLoading && !qmt ? (
            <div className="flex justify-center py-6">
              <Loader2 size={18} className="animate-spin text-text-muted" />
            </div>
          ) : qmt?.online ? (
            <>
              {qmt.market && (
                <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
                  <span className="text-text-muted">市场情绪（studio 盯盘统计）：</span>
                  {qmt.market.index_pct !== null && qmt.market.index_pct !== undefined && (
                    <span className={`num rounded border px-2 py-1 ${pnlColor(qmt.market.index_pct)} ${qmt.market.index_pct >= 0 ? 'border-gain/40 bg-gain/10' : 'border-loss/40 bg-loss/10'}`}>
                      上证 {qmt.market.index_pct >= 0 ? '+' : ''}{qmt.market.index_pct.toFixed(2)}%
                    </span>
                  )}
                  <span className="num rounded border border-gain/40 bg-gain/10 px-2 py-1 text-gain">
                    上涨 {fmt(qmt.market.up_count ?? 0, 0)}
                  </span>
                  <span className="num rounded border border-loss/40 bg-loss/10 px-2 py-1 text-loss">
                    下跌 {fmt(qmt.market.down_count ?? 0, 0)}
                  </span>
                  <span className="num rounded border border-warn/40 bg-warn/10 px-2 py-1 text-warn">
                    涨停 {fmt(qmt.market.limit_up ?? 0, 0)} · 跌停 {fmt(qmt.market.limit_down ?? 0, 0)}
                  </span>
                </div>
              )}
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <QmtStat label="账户" value={String(qmt.account?.accountID ?? '—')} />
                <QmtStat label="总资产" value={`¥${fmt(Number(qmt.account?.balance ?? 0))}`} />
                <QmtStat label="可用资金" value={`¥${fmt(Number(qmt.account?.available ?? 0))}`} />
                <QmtStat label="持仓市值" value={`¥${fmt(Number(qmt.account?.market_value ?? 0))}`} />
              </div>
              {(qmt.positions?.length ?? 0) > 0 && (
                <div className="mt-3 overflow-x-auto rounded-lg border border-border">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-border bg-surface/60 text-text-secondary">
                        <th className="px-3 py-2 text-left font-normal">代码</th>
                        <th className="px-3 py-2 text-left font-normal">名称</th>
                        <th className="px-3 py-2 text-right font-normal">持仓</th>
                        <th className="px-3 py-2 text-right font-normal">成本</th>
                        <th className="px-3 py-2 text-right font-normal">市值</th>
                      </tr>
                    </thead>
                    <tbody>
                      {qmt.positions!.slice(0, 20).map((p, i) => (
                        <tr key={`${p.code}-${i}`} className="border-b border-border/40 last:border-0">
                          <td className="num px-3 py-2 text-teal">{p.code}</td>
                          <td className="px-3 py-2">{String(p.name ?? '—')}</td>
                          <td className="num px-3 py-2 text-right">{fmt(Number(p.volume ?? 0), 0)}</td>
                          <td className="num px-3 py-2 text-right">{fmt(Number(p.cost ?? 0), 3)}</td>
                          <td className="num px-3 py-2 text-right">¥{fmt(Number(p.market_value ?? 0))}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          ) : (
            <p className="text-xs leading-5 text-text-secondary">
              {qmt?.error || 'QMT 未连接'}。
              {qmt?.hint || ''} 连接后这里会显示实时账户资产与持仓（只读，不涉及下单）。
            </p>
          )}
        </div>

        {/* 操作条 */}
        <div className="flex items-center gap-2">
          <button
            onClick={refreshPrices}
            disabled={refreshing}
            className="flex items-center gap-1.5 rounded-lg border border-teal/40 bg-teal/15 px-3 py-2 text-sm text-teal hover:bg-teal/25 disabled:opacity-50"
          >
            <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />
            刷新价格
          </button>
          <button
            onClick={openAdd}
            className="flex items-center gap-1.5 rounded-lg border border-accent/40 bg-accent/15 px-3 py-2 text-sm text-accent hover:bg-accent/25"
          >
            <Plus size={14} /> 添加持仓
          </button>
        </div>

        {/* 表单 */}
        {showForm && (
          <div className="rounded-xl border border-border bg-card p-4 animate-slide-up">
            <h3 className="mb-3 text-sm font-medium">
              {editing ? '编辑持仓' : '添加持仓'}
            </h3>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
              <div className="relative">
                <label className="text-xs text-text-secondary">证券代码</label>
                <input
                  value={searchQ}
                  onChange={(e) => {
                    setSearchQ(e.target.value)
                    setForm((f) => ({ ...f, code: e.target.value.toUpperCase() }))
                  }}
                  placeholder="600519.SH 或 搜索名称"
                  className="num mt-1 w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-accent/50"
                />
                {hints.length > 0 && (
                  <div className="absolute z-10 mt-1 max-h-48 w-full overflow-y-auto rounded-lg border border-border bg-card shadow-xl">
                    {hints.map((h) => (
                      <div
                        key={`${h.type}-${h.code}`}
                        onClick={() => {
                          setForm((f) => ({ ...f, code: h.code, name: h.name }))
                          setSearchQ(h.code)
                          setHints([])
                        }}
                        className="cursor-pointer px-3 py-2 text-sm hover:bg-surface"
                      >
                        <span className="num text-teal">{h.code}</span>
                        <span className="ml-2">{h.name}</span>
                        <span className="ml-2 text-[11px] text-text-muted">{h.type}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <div>
                <label className="text-xs text-text-secondary">名称</label>
                <input
                  value={form.name}
                  onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                  placeholder="自动联想填充"
                  className="mt-1 w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-accent/50"
                />
              </div>
              <div>
                <label className="text-xs text-text-secondary">成本价</label>
                <input
                  type="number"
                  step="0.001"
                  value={form.cost}
                  onChange={(e) => setForm((f) => ({ ...f, cost: e.target.value }))}
                  className="num mt-1 w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-accent/50"
                />
              </div>
              <div>
                <label className="text-xs text-text-secondary">持股数</label>
                <input
                  type="number"
                  step="100"
                  value={form.shares}
                  onChange={(e) => setForm((f) => ({ ...f, shares: e.target.value }))}
                  className="num mt-1 w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-accent/50"
                />
              </div>
            </div>
            <div className="mt-3 flex items-center justify-between">
              <span className="text-xs text-text-muted">
                持仓成本 ={' '}
                {form.cost && form.shares
                  ? `¥${fmt(Number(form.cost) * Number(form.shares))}`
                  : '成本价 × 持股数'}
              </span>
              <div className="flex gap-2">
                <button
                  onClick={() => setShowForm(false)}
                  className="rounded-lg border border-border px-4 py-2 text-sm text-text-secondary hover:text-text-primary"
                >
                  取消
                </button>
                <button
                  onClick={submit}
                  className="rounded-lg bg-accent px-4 py-2 text-sm text-white hover:bg-accent/85"
                >
                  保存
                </button>
              </div>
            </div>
          </div>
        )}

        {/* 明细表 */}
        <div className="overflow-hidden rounded-xl border border-border bg-card">
          {loading ? (
            <div className="flex justify-center py-12">
              <Loader2 size={20} className="animate-spin text-text-muted" />
            </div>
          ) : positions.length === 0 ? (
            <div className="py-14 text-center text-sm text-text-secondary">
              还没有持仓记录，点「添加持仓」开始，或先点「刷新价格」体验真实行情
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-xs text-text-secondary">
                    <th className="px-4 py-3 text-left font-normal">代码 / 名称</th>
                    <th className="px-4 py-3 text-right font-normal">成本价</th>
                    <th className="px-4 py-3 text-right font-normal">最新价</th>
                    <th className="px-4 py-3 text-right font-normal">持股</th>
                    <th className="px-4 py-3 text-right font-normal">成本市值</th>
                    <th className="px-4 py-3 text-right font-normal">当前市值</th>
                    <th className="px-4 py-3 text-right font-normal">盈亏</th>
                    <th className="px-4 py-3 text-right font-normal">涨跌幅</th>
                    <th className="px-4 py-3 text-right font-normal">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {positions.map((p) => (
                    <tr key={p.id} className="border-b border-border/50 last:border-0">
                      <td className="px-4 py-3">
                        <div className="num text-teal">{p.code}</div>
                        <div className="text-xs text-text-secondary">{p.name || '—'}</div>
                      </td>
                      <td className="num px-4 py-3 text-right">{fmt(p.cost, 3)}</td>
                      <td className="num px-4 py-3 text-right">
                        {p.cur_price !== null ? fmt(p.cur_price, 3) : (
                          <span className="text-text-muted">待更新</span>
                        )}
                      </td>
                      <td className="num px-4 py-3 text-right">{fmt(p.shares, 0)}</td>
                      <td className="num px-4 py-3 text-right">¥{fmt(p.cost_value)}</td>
                      <td className="num px-4 py-3 text-right">
                        {p.cur_value !== null ? `¥${fmt(p.cur_value)}` : '—'}
                      </td>
                      <td className={`num px-4 py-3 text-right ${pnlColor(p.pnl)}`}>
                        {p.pnl !== null ? fmt(p.pnl) : '—'}
                      </td>
                      <td className={`num px-4 py-3 text-right ${pnlColor(p.pnl_pct)}`}>
                        {p.pnl_pct !== null ? fmtPct(p.pnl_pct) : '—'}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <div className="group flex items-center justify-end gap-2">
                          <button
                            onClick={() => openEdit(p)}
                            className="text-text-muted opacity-0 transition hover:text-accent group-hover:opacity-100"
                          >
                            <Pencil size={14} />
                          </button>
                          <button
                            onClick={() => remove(p)}
                            className="text-text-muted opacity-0 transition hover:text-loss group-hover:opacity-100"
                          >
                            <Trash2 size={14} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <p className="pb-4 text-center text-[11px] text-text-muted">
          行情来自 akshare 公开接口（收盘价），数据仅供研究参考
        </p>
      </div>
    </div>
  )
}

function QmtStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border/70 bg-surface p-3">
      <div className="text-xs text-text-secondary">{label}</div>
      <div className="num mt-1 text-base font-semibold">{value}</div>
    </div>
  )
}

function SummaryCard({
  icon,
  label,
  value,
  valueClass = '',
  note,
}: {
  icon: React.ReactNode
  label: string
  value: string
  valueClass?: string
  note?: string
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <div className="flex items-center gap-1.5 text-xs text-text-secondary">
        {icon}
        {label}
      </div>
      <div className={`num mt-1.5 text-xl font-semibold ${valueClass}`}>{value}</div>
      {note && <div className="mt-0.5 text-[11px] text-text-muted">{note}</div>}
    </div>
  )
}
