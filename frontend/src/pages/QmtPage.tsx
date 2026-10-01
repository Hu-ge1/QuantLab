import {
  Bell,
  Download,
  Loader2,
  Plug,
  RefreshCw,
  ShieldAlert,
  Wrench,
} from 'lucide-react'
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  bridgeApi,
  tradingApi,
  type AuditEntry,
  type BridgeState,
  type TickPoint,
  type TradingStatus,
} from '../lib/api'
import { fmt, fmtPct, pnlColor } from '../lib/utils'
import { useToast } from '../components/Toast'

const TABS = ['交易', '持仓', '委托', '成交', '信号', '日报'] as const
type Tab = (typeof TABS)[number]

/** 提醒音（WebAudio 短促双响） */
function beep() {
  try {
    const ctx = new AudioContext()
    for (const [freq, delay] of [[880, 0], [1180, 0.18]] as const) {
      const o = ctx.createOscillator()
      const g = ctx.createGain()
      o.connect(g)
      g.connect(ctx.destination)
      o.frequency.value = freq
      g.gain.value = 0.06
      o.start(ctx.currentTime + delay)
      o.stop(ctx.currentTime + delay + 0.12)
    }
  } catch { /* 音频不可用静默 */ }
}

/** 宽松归一化：600519 / 600519.SH / SH600519 → 600519.SH */
function normalizeCode(raw: string): string {
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

export default function QmtPage() {
  const toast = useToast()
  const [state, setState] = useState<BridgeState | null>(null)
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<Tab>('交易')
  const [watchlist, setWatchlist] = useState<string[]>([])
  const [activeCode, setActiveCode] = useState('')
  const [ticks, setTicks] = useState<TickPoint[]>([])
  const [installing, setInstalling] = useState(false)
  const [tstatus, setTstatus] = useState<TradingStatus | null>(null)
  const [sigFilter, setSigFilter] = useState<'all' | 'trade' | 'radar'>('all')
  // 下单表单状态提升：持仓行内「买/卖」可自动填单
  const [orderForm, setOrderForm] = useState({
    code: '', side: 'buy', prType: 'limit', price: '', volume: '',
  })
  const [savedWatch, setSavedWatch] = useState<string[]>([])
  const [newCode, setNewCode] = useState('')
  const [soundOn, setSoundOn] = useState(false)
  const [notifyOn, setNotifyOn] = useState(false)
  const lastSigTimeRef = useRef('')
  const timerRef = useRef<number | null>(null)

  const load = useCallback(async () => {
    try {
      const s = await bridgeApi.state()
      setState(s)
      // 新信号提醒：提醒音 + 桌面通知（需要用户手动开启）
      const top = s.signals?.[0]
      if (top && lastSigTimeRef.current && top.time !== lastSigTimeRef.current) {
        if (soundOn) beep()
        if (notifyOn && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
          try {
            new Notification('QuantLab 新信号', {
              body: `${top.time.slice(11)} ${top.strategy} ${top.code} ${top.action} ${top.note ?? ''}`.slice(0, 120),
            })
          } catch { /* 通知失败静默 */ }
        }
      }
      if (top) lastSigTimeRef.current = top.time
    } catch {
      /* 服务未启动时静默 */
    } finally {
      setLoading(false)
    }
  }, [soundOn, notifyOn])

  const loadTrading = useCallback(async () => {
    try {
      setTstatus(await tradingApi.status())
    } catch {
      /* 静默 */
    }
  }, [])

  useEffect(() => {
    load()
    loadTrading()
    timerRef.current = window.setInterval(() => {
      load()
      loadTrading()
    }, 3000)
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current)
    }
  }, [load, loadTrading])

  const loadWatch = useCallback(async () => {
    // 自选清单以保存的 watchlist 为准，辅以桥接正在推的 tick 代码
    try {
      const { codes } = await bridgeApi.watchlistGet()
      setSavedWatch(codes || [])
      if (codes?.length && !activeCode) setActiveCode(codes[0])
    } catch {
      /* ignore */
    }
    try {
      const s = await bridgeApi.state()
      const tickCodes = extractWatchlist(s)
      if (tickCodes.length > 0 && !activeCode) setActiveCode(tickCodes[0])
    } catch {
      /* ignore */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const saveWatch = async (codes: string[]) => {
    const cleaned = [...new Set(codes.map(normalizeCode).filter(Boolean))]
    try {
      const r = await bridgeApi.watchlist(cleaned)
      setSavedWatch(r.codes)
      if (r.codes.length > 0 && !activeCode) setActiveCode(r.codes[0])
      toast.success(`自选已保存（${r.codes.length} 只），桥接下一轮生效`)
    } catch (e) {
      toast.error(`保存失败：${e}`)
    }
  }

  useEffect(() => {
    loadWatch()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!activeCode) return
    let alive = true
    const fetchTicks = async () => {
      try {
        const r = await bridgeApi.ticks(activeCode)
        if (alive) setTicks(r.points || [])
      } catch {
        /* ignore */
      }
    }
    fetchTicks()
    const t = window.setInterval(fetchTicks, 5000)
    return () => {
      alive = false
      window.clearInterval(t)
    }
  }, [activeCode])

  const install = async () => {
    if (!window.confirm(
      '将把桥接策略重新写入 QMT 策略目录（自动备份旧版，指向 QuantLab :8000）。\n' +
      '写入后需在 QMT 中停止旧桥接策略并重新运行。确认？',
    )) return
    setInstalling(true)
    try {
      const r = await bridgeApi.install()
      if (r.ok) {
        toast.success(`桥接已写入 ${r.path}，注册${r.registered ? '成功' : '失败：' + r.register_msg}`)
      } else {
        toast.error(r.error || '安装失败')
      }
    } catch (e) {
      toast.error(`安装失败：${e}`)
    } finally {
      setInstalling(false)
    }
  }

  const st = state?.state || {}
  const acc = (st.account || {}) as Record<string, number | string>
  const market = state?.market_history?.[state.market_history.length - 1]
  const positions = (st.positions || []) as Record<string, number | string>[]
  const orders = (st.orders || []) as Record<string, number | string>[]
  const deals = (st.deals || []) as Record<string, number | string>[]
  const signals = state?.signals || []
  const ah = state?.asset_history ?? []
  const dayPnl = ah.length > 0 ? ah[ah.length - 1].total - ah[0].total : null
  const dayPnlPct = dayPnl !== null && ah[0].total > 0 ? (dayPnl / ah[0].total) * 100 : 0

  return (
    <div className="h-full overflow-y-auto p-5">
      <div className="mx-auto max-w-6xl space-y-4">
        {/* 状态条 */}
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {loading && !state ? (
            <Loader2 size={14} className="animate-spin text-text-muted" />
          ) : state?.online ? (
            <>
              <span className="rounded border border-gain/40 bg-gain/10 px-2 py-1 text-gain">
                ● 桥接在线
              </span>
              {(() => {
                const accId = String(st?.account?.accountID ?? '')
                if (!accId) return null
                const isSim = /test/i.test(accId)
                return (
                  <span className={`rounded border px-2 py-1 font-medium ${
                    isSim ? 'border-teal/40 bg-teal/10 text-teal' : 'border-loss/40 bg-loss/10 text-loss'
                  }`}>
                    {isSim ? '⟳ 模拟盘' : '⚠ 实盘'}
                    <span className="ml-1 font-normal opacity-75">{accId}</span>
                  </span>
                )
              })()}
              <span className="text-text-muted">
                最近心跳 {state.last_seen.slice(11, 19)} · 模式 {String(st.mode || '—')}
              </span>
            </>
          ) : (
            <span className="rounded border border-warn/40 bg-warn/10 px-2 py-1 text-warn">
              ○ 桥接离线（在 QMT 中运行桥接策略后自动连上）
            </span>
          )}
          <span className={`rounded border px-2 py-1 ${state?.session?.trading ? 'border-gain/40 bg-gain/10 text-gain' : 'border-border text-text-secondary'}`}>
            {state?.session?.label || '—'}
          </span>
          <span className="rounded border border-teal/40 bg-teal/10 px-2 py-1 text-teal">
            <Plug size={10} className="mr-1 inline" />
            桥接已内置（推送至 :8000）
          </span>
          <div className="ml-auto flex items-center gap-2">
            <button
              onClick={install}
              disabled={installing}
              className="flex items-center gap-1 rounded-lg border border-border bg-card px-2.5 py-1.5 text-text-secondary hover:text-text-primary disabled:opacity-50"
            >
              {installing ? <Loader2 size={12} className="animate-spin" /> : <Wrench size={12} />}
              重装桥接到 QMT
            </button>
            <button
              onClick={load}
              className="flex items-center gap-1 rounded-lg border border-border bg-card px-2.5 py-1.5 text-text-secondary hover:text-text-primary"
            >
              <RefreshCw size={12} /> 刷新
            </button>
          </div>
        </div>

        {/* 资金卡 */}
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
          <Stat label="总资产" value={`¥${fmt(Number(acc.balance ?? 0))}`} />
          <Stat label="可用资金" value={`¥${fmt(Number(acc.available ?? 0))}`} />
          <Stat label="持仓市值" value={`¥${fmt(Number(acc.market_value ?? 0))}`} />
          <Stat label="持仓盈亏" value={fmt(Number(acc.profit ?? 0))} valueClass={pnlColor(Number(acc.profit ?? 0))} />
          {dayPnl !== null && (
            <Stat
              label="当日盈亏"
              value={`${dayPnl >= 0 ? '+' : ''}${fmt(dayPnl)}`}
              valueClass={pnlColor(dayPnl)}
              note={`${fmtPct(dayPnlPct)}（vs 当日首点）`}
            />
          )}
        </div>

        {/* 当日资产曲线（桥接心跳每 2 秒一点） */}
        {(state?.asset_history?.length ?? 0) > 1 && (
          <div className="rounded-xl border border-border bg-card p-4">
            <div className="mb-2 flex items-center justify-between">
              <h3 className="text-sm font-medium">当日资产曲线（桥接实时推送）</h3>
              <span className="text-[11px] text-text-muted">
                {state!.asset_history![0].t} → {state!.asset_history![state!.asset_history!.length - 1].t} ·{' '}
                {state!.asset_history!.length} 点
              </span>
            </div>
            <div style={{ height: 170 }}>
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={state!.asset_history}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e2d47" />
                  <XAxis dataKey="t" tick={{ fill: '#475569', fontSize: 10 }} tickLine={false} minTickGap={50} />
                  <YAxis
                    domain={['auto', 'auto']}
                    tick={{ fill: '#475569', fontSize: 10 }}
                    tickLine={false}
                    tickFormatter={(v: number) => `${(v / 10000).toFixed(1)}万`}
                  />
                  <Tooltip
                    contentStyle={{ background: '#111e33', border: '1px solid #1e2d47', borderRadius: 8, fontSize: 12 }}
                    formatter={(v: number, name: string) => [`¥${fmt(v)}`, name === 'total' ? '总资产' : name === 'avail' ? '可用' : '市值']}
                  />
                  <Line type="monotone" dataKey="total" stroke="#14b8a6" strokeWidth={2} dot={false} name="total" />
                  <Line type="monotone" dataKey="avail" stroke="#3b82f6" strokeWidth={1} strokeDasharray="4 4" dot={false} name="avail" />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        )}

        {/* 市场情绪 */}
        {market && (
          <div className="flex flex-wrap items-center gap-2 rounded-xl border border-border bg-card px-4 py-3 text-xs">
            <span className="text-text-muted">市场情绪：</span>
            {market.index_pct !== null && market.index_pct !== undefined && (
              <span className={`num rounded border px-2 py-1 ${Number(market.index_pct) >= 0 ? 'border-gain/40 bg-gain/10 text-gain' : 'border-loss/40 bg-loss/10 text-loss'}`}>
                上证 {Number(market.index_pct) >= 0 ? '+' : ''}{Number(market.index_pct).toFixed(2)}%
              </span>
            )}
            <span className="num rounded border border-gain/40 bg-gain/10 px-2 py-1 text-gain">
              上涨 {fmt(Number(market.up ?? 0), 0)}
            </span>
            <span className="num rounded border border-loss/40 bg-loss/10 px-2 py-1 text-loss">
              下跌 {fmt(Number(market.down ?? 0), 0)}
            </span>
            <span className="num rounded border border-warn/40 bg-warn/10 px-2 py-1 text-warn">
              涨停 {fmt(Number(market.limit_up ?? 0), 0)} · 跌停 {fmt(Number(market.limit_down ?? 0), 0)}
            </span>
            <span className="text-text-muted">{market.time}</span>
          </div>
        )}

        {/* 自选股 + 分时图（桥接按自选推送 tick，雷达同步覆盖） */}
        {(watchlist.length > 0 || savedWatch.length > 0) && (
          <div className="rounded-xl border border-border bg-card p-4">
            <div className="mb-2 flex flex-wrap items-center gap-1.5">
              <span className="text-sm font-medium">自选分时</span>
              {[...new Set([...savedWatch, ...watchlist])].map((c) => (
                <span
                  key={c}
                  className={`num group flex items-center gap-1 rounded border px-2 py-1 text-xs ${
                    activeCode === c ? 'border-teal/50 bg-teal/15 text-teal' : 'border-border bg-surface text-text-secondary'
                  }`}
                >
                  <button onClick={() => setActiveCode(c)}>{c}</button>
                  <button
                    onClick={() => saveWatch(savedWatch.filter((x) => x !== c))}
                    className="text-text-muted opacity-0 transition group-hover:opacity-100 hover:text-loss"
                    title="从自选移除"
                  >
                    ×
                  </button>
                </span>
              ))}
              <input
                value={newCode}
                onChange={(e) => setNewCode(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && newCode.trim()) {
                    const c = normalizeCode(newCode)
                    if (c) {
                      saveWatch([...savedWatch, c])
                      setActiveCode(c)
                      setNewCode('')
                    } else toast.error('代码格式应为 600519.SH 或 600519')
                  }
                }}
                placeholder="加自选：600519.SH ↵"
                className="num w-36 rounded border border-border bg-surface px-2 py-1 text-xs outline-none focus:border-teal/50"
              />
            </div>
            {ticks.length > 1 ? (
              <div style={{ height: 220 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={ticks}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e2d47" />
                    <XAxis dataKey="t" tick={{ fill: '#475569', fontSize: 10 }} tickLine={false} minTickGap={40} />
                    <YAxis domain={['auto', 'auto']} tick={{ fill: '#475569', fontSize: 10 }} tickLine={false} />
                    <Tooltip
                      contentStyle={{ background: '#111e33', border: '1px solid #1e2d47', borderRadius: 8, fontSize: 12 }}
                      formatter={(v: number) => [`${fmt(v, 3)}`, '价格']}
                    />
                    <Line type="monotone" dataKey="p" stroke="#14b8a6" strokeWidth={2} dot={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <p className="py-6 text-center text-xs text-text-muted">
                暂无 {activeCode} 的分时数据（盘中桥接推送后自动积累）
              </p>
            )}
          </div>
        )}

        {/* 数据 Tabs */}
        <div className="overflow-hidden rounded-xl border border-border bg-card">
          <div className="flex border-b border-border">
            {TABS.map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`px-4 py-2.5 text-sm ${tab === t ? 'border-b-2 border-teal text-teal' : 'text-text-secondary hover:text-text-primary'}`}
              >
                {t}
                {t === '信号' && signals.length > 0 && (
                  <span className="ml-1 rounded bg-warn/20 px-1 text-[10px] text-warn">{signals.length}</span>
                )}
              </button>
            ))}
            <div className="ml-auto flex items-center gap-2 px-3">
              {tab === '信号' && (
                <a
                  href="/api/bridge/export/signals.csv"
                  className="flex items-center gap-1 text-xs text-text-secondary hover:text-teal"
                >
                  <Download size={12} /> 导出 CSV
                </a>
              )}
              {tab === '成交' && (
                <a
                  href="/api/bridge/export/deals.csv"
                  className="flex items-center gap-1 text-xs text-text-secondary hover:text-teal"
                >
                  <Download size={12} /> 导出 CSV
                </a>
              )}
            </div>
          </div>

          <div className="max-h-96 overflow-auto">
            {tab === '交易' && (
              <TradingPanel
                tstatus={tstatus}
                reloadTrading={loadTrading}
                signals={signals}
                order={orderForm}
                setOrder={setOrderForm}
              />
            )}
            {tab === '持仓' && (
              positions.length > 0 ? (
                <Table
                  headers={['代码', '名称', '持仓', '可用', '成本', '市值', '盈亏', '快捷下单']}
                  rows={positions.map((p) => {
                    const vol = Number(p.volume ?? 0)
                    const cost = Number(p.open_price ?? p.cost ?? 0)
                    const mv = Number(p.market_value ?? 0)
                    const profit = Number(p.position_profit ?? p.profit ?? (cost > 0 ? mv - cost * vol : 0))
                    return [
                      <span key="c" className="num text-teal">{String(p.code ?? '')}</span>,
                      String(p.name ?? '—'),
                      <span key="v" className="num">{fmt(vol, 0)}</span>,
                      <span key="cu" className="num">{fmt(Number(p.can_use_volume ?? p.can_use ?? 0), 0)}</span>,
                      <span key="cp" className="num">{fmt(cost, 3)}</span>,
                      <span key="mv" className="num">¥{fmt(mv)}</span>,
                      <span key="pn" className={`num ${pnlColor(profit)}`}>{fmt(profit)}</span>,
                      <span key="op" className="flex justify-end gap-1">
                        <button
                          onClick={() => {
                            setOrderForm({
                              code: String(p.code ?? ''), side: 'buy', prType: 'limit',
                              price: '', volume: String(Math.max(100, vol)),
                            })
                            setTab('交易')
                          }}
                          className="rounded border border-loss/40 bg-loss/10 px-1.5 py-0.5 text-[11px] text-loss"
                        >
                          买
                        </button>
                        <button
                          onClick={() => {
                            const canUse = Number(p.can_use_volume ?? p.can_use ?? vol)
                            setOrderForm({
                              code: String(p.code ?? ''), side: 'sell', prType: 'limit',
                              price: '', volume: String(Math.max(100, canUse)),
                            })
                            setTab('交易')
                          }}
                          className="rounded border border-gain/40 bg-gain/10 px-1.5 py-0.5 text-[11px] text-gain"
                        >
                          卖
                        </button>
                      </span>,
                    ]
                  })}
                />
              ) : <Empty text="暂无持仓（桥接在线后显示 QMT 实盘持仓）" />
            )}
            {tab === '委托' && (
              orders.length > 0 ? (
                <Table
                  headers={['时间', '代码', '方向', '价格', '数量', '状态']}
                  rows={orders.map((o, i) => [
                    String(o.time ?? '—'),
                    <span key="c" className="num text-teal">{String(o.code ?? o.stock_code ?? '')}</span>,
                    String(o.side ?? ''),
                    <span key="p" className="num">{fmt(Number(o.price ?? 0), 3)}</span>,
                    <span key="v" className="num">{fmt(Number(o.volume ?? 0), 0)}</span>,
                    String(o.status ?? o.state ?? '—'),
                  ])}
                />
              ) : <Empty text="暂无委托记录" />
            )}
            {tab === '成交' && (
              deals.length > 0 ? (
                <Table
                  headers={['时间', '代码', '成交价', '成交量', '成交编号']}
                  rows={deals.map((d, i) => [
                    String(d.time ?? '—'),
                    <span key="c" className="num text-teal">{String(d.code ?? '')}</span>,
                    <span key="p" className="num">{fmt(Number(d.price ?? 0), 3)}</span>,
                    <span key="v" className="num">{fmt(Number(d.volume ?? 0), 0)}</span>,
                    String(d.deal_id ?? ''),
                  ])}
                />
              ) : <Empty text="暂无成交记录" />
            )}
            {tab === '信号' && (
              signals.length > 0 ? (
                <>
                  <div className="flex flex-wrap items-center gap-1.5 px-4 pt-3">
                    {([
                      ['all', '全部'],
                      ['trade', '交易信号'],
                      ['radar', '雷达/风控'],
                    ] as const).map(([k, label]) => (
                      <button
                        key={k}
                        onClick={() => setSigFilter(k)}
                        className={`rounded border px-2 py-1 text-[11px] ${
                          sigFilter === k ? 'border-teal/50 bg-teal/15 text-teal' : 'border-border text-text-secondary'
                        }`}
                      >
                        {label}
                      </button>
                    ))}
                    <div className="ml-auto flex items-center gap-1.5">
                      <button
                        onClick={() => {
                          setSoundOn(!soundOn)
                          if (!soundOn) toast.success('提醒音已开启：新信号会“叮咚”')
                        }}
                        className={`rounded border px-2 py-1 text-[11px] ${soundOn ? 'border-teal/50 bg-teal/15 text-teal' : 'border-border text-text-secondary'}`}
                      >
                        🔔 提醒音
                      </button>
                      <button
                        onClick={async () => {
                          if (!notifyOn && typeof Notification !== 'undefined' && Notification.permission !== 'granted') {
                            const perm = await Notification.requestPermission()
                            if (perm !== 'granted') return toast.error('浏览器拒绝了通知权限')
                          }
                          setNotifyOn(!notifyOn)
                          toast.success(!notifyOn ? '桌面通知已开启' : '桌面通知已关闭')
                        }}
                        className={`rounded border px-2 py-1 text-[11px] ${notifyOn ? 'border-teal/50 bg-teal/15 text-teal' : 'border-border text-text-secondary'}`}
                      >
                        💬 桌面通知
                      </button>
                    </div>
                  </div>
                  <div className="max-h-64 divide-y divide-border/40 overflow-y-auto">
                    {signals
                      .filter((s) =>
                        sigFilter === 'all'
                          ? true
                          : sigFilter === 'trade'
                            ? s.action === 'buy' || s.action === 'sell'
                            : !(s.action === 'buy' || s.action === 'sell'),
                      )
                      .map((s, i) => (
                        <div key={i} className="flex items-center gap-2 px-4 py-2 text-xs">
                          <Bell size={12} className={s.action === 'buy' ? 'text-loss' : s.action === 'sell' || s.action === 'halt' ? 'text-gain' : 'text-warn'} />
                          <span className="text-text-muted">{s.time?.slice(11)}</span>
                          <span className="text-text-secondary">{s.strategy}</span>
                          <span className="num text-teal">{s.code}</span>
                          <span className={s.action === 'buy' ? 'text-loss' : 'text-gain'}>{s.action}</span>
                          {s.price !== null && s.price !== undefined && (
                            <span className="num">{fmt(Number(s.price), 2)}</span>
                          )}
                          <span className="ml-auto truncate text-text-muted">{s.note}</span>
                        </div>
                      ))}
                  </div>
                </>
              ) : <Empty text="暂无策略信号（运行带 _report_signal 的策略后出现在这里）" />
            )}
            {tab === '日报' && <ReportsView />}
          </div>
        </div>

        {/* 交易安全说明 */}
        <div className="rounded-xl border border-warn/30 bg-warn/5 p-4 text-xs leading-5 text-text-secondary">
          <div className="mb-1 flex items-center gap-1.5 font-medium text-warn">
            <ShieldAlert size={14} /> 交易安全边界
          </div>
          下单走「交易」Tab：每笔指令过服务端强制风控链（总闸 → 允许开关 → 交易时段 →
          白名单 → 单笔上限 → 每日次数），全程审计留痕。全自动（L3）默认关闭，
          开启后受当日亏损熔断保护；紧急情况点红色总闸一键停掉一切下单。
          <b>历史回测不代表未来收益，自动交易风险自担。</b>
        </div>
      </div>
    </div>
  )
}

function extractWatchlist(s: BridgeState | null): string[] {
  // 自选列表优先取桥接推送 ticks 的代码
  const tickCodes = Object.keys(s?.state?.ticks || {})
  if (tickCodes.length > 0) return tickCodes.slice(0, 12)
  return []
}

function Stat({ label, value, valueClass = '', note }: { label: string; value: string; valueClass?: string; note?: string }) {
  return (
    <div className="rounded-lg border border-border/70 bg-surface p-3">
      <div className="text-xs text-text-secondary">{label}</div>
      <div className={`num mt-1 text-base font-semibold ${valueClass}`}>{value}</div>
      {note && <div className="mt-0.5 text-[11px] text-text-muted">{note}</div>}
    </div>
  )
}

// ── 交易面板（L1 手动 / L2 信号执行 / L3 全自动配置）────────────────────────

interface TStatus {
  trading_enabled: boolean
  allow_order: boolean
  auto_trading: boolean
  circuit_loss_pct: number
  order_max_volume: number
  order_max_per_day: number
  order_allowlist: string[]
  auto_rules: { strategy: string; code: string; side: string; volume: number }[]
  today_orders: number
  trading_phase: { trading: boolean; label: string }
  bridge_online: boolean
}

interface OrderForm {
  code: string
  side: string
  prType: string
  price: string
  volume: string
}

function TradingPanel({
  tstatus,
  reloadTrading,
  signals,
  order,
  setOrder,
}: {
  tstatus: TradingStatus | null
  reloadTrading: () => void
  signals: { time: string; strategy: string; code: string; action: string; price?: number | null; note?: string }[]
  order: OrderForm
  setOrder: React.Dispatch<React.SetStateAction<OrderForm>>
}) {
  const toast = useToast()
  const [placing, setPlacing] = useState(false)
  const [execSignal, setExecSignal] = useState<{ code: string; action: string; price?: number | null; strategy: string } | null>(null)
  const [execVolume, setExecVolume] = useState('100')
  const [executing, setExecuting] = useState(false)
  const [rules, setRules] = useState<{ strategy: string; code: string; side: string; volume: string }[]>([])
  const [allowlistText, setAllowlistText] = useState('')
  const [circuit, setCircuit] = useState('2')
  const [maxVol, setMaxVol] = useState('')
  const [maxPerDay, setMaxPerDay] = useState('')

  const ts = tstatus
  useEffect(() => {
    if (!ts) return
    setRules(ts.auto_rules.map((r) => ({ ...r, volume: String(r.volume) })))
    setAllowlistText((ts.order_allowlist || []).join(','))
    setCircuit(String(ts.circuit_loss_pct ?? 2))
    setMaxVol(String(ts.order_max_volume ?? 2000))
    setMaxPerDay(String(ts.order_max_per_day ?? 20))
  }, [ts])

  const saveLimits = async () => {
    const r = await tradingApi.updateConfig({
      order_max_volume: Number(maxVol) || undefined,
      order_max_per_day: Number(maxPerDay) || undefined,
      circuit_loss_pct: Number(circuit) || 0,
      order_allowlist: allowlistText,
    })
    toast[r.ok ? 'success' : 'error'](r.ok ? '风控额度已保存' : r.error || '保存失败')
    reloadTrading()
  }

  const toggleAllowOrder = async () => {
    if (!ts) return
    if (ts.allow_order && !window.confirm('关闭「允许下单」后所有下单都会被拦截。确认关闭？')) return
    if (!ts.allow_order && !window.confirm('确认打开「允许下单」？开启后风控链内的一切合法下单都会真实执行。')) return
    const r = await tradingApi.updateConfig({ allow_order: !ts.allow_order })
    toast[r.ok ? 'success' : 'error'](r.ok ? (ts.allow_order ? '已关闭允许下单' : '已开启允许下单') : r.error || '操作失败')
    reloadTrading()
  }

  const toggleAuto = async () => {
    if (!ts) return
    if (ts.auto_trading
      ? !window.confirm('确认关闭全自动交易？（已排队的指令仍会执行完）')
      : !window.confirm('确认开启全自动交易？\n\n开启后：策略信号一旦命中规则表，将自动生成真实订单，无需人工确认！\n建议先小股数规则试跑。')) return
    const r = await tradingApi.updateConfig({ auto_trading: !ts.auto_trading })
    if (!r.ok && r.error) toast.error(r.error)
    else toast[r.ok ? 'success' : 'error'](r.ok ? (ts.auto_trading ? '全自动已关闭' : '全自动已开启（受熔断保护）') : '操作失败')
    reloadTrading()
  }

  const kill = async () => {
    if (!window.confirm('【紧急停机】确认拉下交易总闸？\n\n将立即拦截一切下单指令并停用全自动交易。')) return
    await tradingApi.kill()
    toast.success('总闸已关闭，一切下单已被拦截')
    reloadTrading()
  }

  const reenable = async () => {
    if (!window.confirm('确认重新启用交易总闸？')) return
    await tradingApi.enable()
    toast.success('总闸已启用')
    reloadTrading()
  }

  const placeOrder = async () => {
    const vol = Number(order.volume)
    if (!order.code.trim()) return toast.error('请填写代码')
    if (!vol || vol <= 0 || vol % 100 !== 0) return toast.error('股数必须为 100 的整数倍')
    if (order.prType === 'limit' && (!order.price || Number(order.price) <= 0)) return toast.error('限价单需要有效价格')
    if (!window.confirm(
      `确认下单？\n\n${order.side === 'buy' ? '买入' : '卖出'} ${order.code} ${vol} 股\n` +
      `${order.prType === 'limit' ? `限价 ¥${order.price}` : '市价'}\n\n（仍会过服务端风控链）`,
    )) return
    setPlacing(true)
    try {
      const r = await tradingApi.order({
        code: order.code.trim().toUpperCase(),
        side: order.side,
        prType: order.prType,
        price: order.prType === 'limit' ? Number(order.price) : -1,
        volume: vol,
      })
      if (r.ok) {
        toast.success(`指令已入队（${r.id}），桥接领取后真实执行`)
        reloadTrading()
      } else {
        toast.error(r.error || '下单被拦截')
      }
    } catch (e) {
      toast.error(`下单失败：${e}`)
    } finally {
      setPlacing(false)
    }
  }

  const execOne = async () => {
    if (!execSignal) return
    const vol = Number(execVolume)
    if (!vol || vol <= 0 || vol % 100 !== 0) return toast.error('股数必须为 100 的整数倍')
    setExecuting(true)
    try {
      const r = await tradingApi.signalExec({
        code: execSignal.code, action: execSignal.action,
        price: execSignal.price ?? undefined, volume: vol, strategy: execSignal.strategy,
      })
      if (r.ok) toast.success(`信号已转订单（${r.id}）`)
      else toast.error(r.error || '执行被拦截')
    } finally {
      setExecuting(false)
    }
  }

  const saveRules = async () => {
    const clean = rules
      .map((r) => ({ strategy: r.strategy.trim() || '*', code: r.code.trim().toUpperCase() || '*', side: r.side, volume: Number(r.volume) || 0 }))
      .filter((r) => r.volume > 0)
    const r = await tradingApi.setRules(clean)
    toast[r.ok ? 'success' : 'error'](r.ok ? `已保存 ${clean.length} 条自动执行规则` : '保存失败')
    reloadTrading()
  }

  if (!ts) {
    return <div className="py-10 text-center text-sm text-text-secondary">加载交易状态…</div>
  }

  const tradable = ts.trading_enabled && ts.allow_order && ts.bridge_online

  return (
    <div className="space-y-4 p-4">
      {/* 状态条 + 总闸 */}
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className={`rounded border px-2 py-1 ${ts.trading_enabled ? 'border-gain/40 bg-gain/10 text-gain' : 'border-loss/50 bg-loss/15 text-loss'}`}>
          总闸：{ts.trading_enabled ? '开启' : '已关闭（拦截一切下单）'}
        </span>
        <span className={`rounded border px-2 py-1 ${ts.allow_order ? 'border-warn/40 bg-warn/10 text-warn' : 'border-border text-text-secondary'}`}>
          允许下单：{ts.allow_order ? '开' : '关'}
        </span>
        <span className={`rounded border px-2 py-1 ${ts.auto_trading ? 'border-loss/50 bg-loss/15 text-loss font-semibold' : 'border-border text-text-secondary'}`}>
          全自动：{ts.auto_trading ? '● 运行中' : '关'}
        </span>
        {(ts.queued_commands > 0 || ts.inflight_commands > 0) && (
          <span className="rounded border border-warn/40 bg-warn/10 px-2 py-1 text-warn">
            指令：排队 {ts.queued_commands} / 待确认 {ts.inflight_commands}
          </span>
        )}
        <span className={`rounded border px-2 py-1 ${ts.bridge_online ? 'border-gain/40 bg-gain/10 text-gain' : 'border-border text-text-secondary'}`}>
          桥接：{ts.bridge_online ? '在线' : '离线'}
        </span>
        <span className="rounded border border-border px-2 py-1 text-text-secondary">
          今日下单 {ts.today_orders} / {ts.order_max_per_day} 笔 · {ts.trading_phase.label}
        </span>
        <div className="ml-auto">
          {ts.trading_enabled ? (
            <button onClick={kill} className="rounded-lg border border-loss/50 bg-loss/15 px-3 py-1.5 text-xs font-semibold text-loss hover:bg-loss/25">
              ⏹ 紧急停机（拉总闸）
            </button>
          ) : (
            <button onClick={reenable} className="rounded-lg border border-gain/50 bg-gain/15 px-3 py-1.5 text-xs font-semibold text-gain hover:bg-gain/25">
              重新启用总闸
            </button>
          )}
        </div>
      </div>

      {/* L1 手动下单 */}
      <div className="rounded-lg border border-border bg-surface p-4">
        <div className="mb-2 flex items-center justify-between">
          <h4 className="text-xs font-semibold text-text-secondary">L1 · 手动下单（每笔需弹窗确认，服务端再过一遍风控）</h4>
          <button
            onClick={toggleAllowOrder}
            className={`rounded px-2 py-1 text-[11px] ${ts.allow_order ? 'bg-warn/20 text-warn' : 'bg-card text-text-secondary border border-border'}`}
          >
            允许下单：{ts.allow_order ? '开' : '关'}（点击切换）
          </button>
        </div>
        <div className="grid grid-cols-2 gap-2 md:grid-cols-6">
          <input
            value={order.code}
            onChange={(e) => setOrder((f) => ({ ...f, code: e.target.value.toUpperCase() }))}
            placeholder="600519.SH"
            className="num rounded border border-border bg-card px-2 py-1.5 text-xs outline-none focus:border-accent/50"
          />
          <select
            value={order.side}
            onChange={(e) => setOrder((f) => ({ ...f, side: e.target.value }))}
            className="rounded border border-border bg-card px-2 py-1.5 text-xs outline-none"
          >
            <option value="buy">买入</option>
            <option value="sell">卖出</option>
          </select>
          <select
            value={order.prType}
            onChange={(e) => setOrder((f) => ({ ...f, prType: e.target.value }))}
            className="rounded border border-border bg-card px-2 py-1.5 text-xs outline-none"
          >
            <option value="limit">限价</option>
            <option value="market">市价</option>
          </select>
          <input
            type="number"
            value={order.price}
            onChange={(e) => setOrder((f) => ({ ...f, price: e.target.value }))}
            placeholder="价格"
            disabled={order.prType !== 'limit'}
            className="num rounded border border-border bg-card px-2 py-1.5 text-xs outline-none disabled:opacity-40"
          />
          <input
            type="number"
            step={100}
            value={order.volume}
            onChange={(e) => setOrder((f) => ({ ...f, volume: e.target.value }))}
            placeholder="股数(整百)"
            className="num rounded border border-border bg-card px-2 py-1.5 text-xs outline-none"
          />
          <button
            onClick={placeOrder}
            disabled={placing || !tradable}
            className={`rounded py-1.5 text-xs font-medium text-white disabled:opacity-40 ${order.side === 'buy' ? 'bg-loss hover:bg-loss/85' : 'bg-gain hover:bg-gain/85'}`}
            title={!tradable ? `不可交易：${!ts.trading_enabled ? '总闸关闭' : !ts.allow_order ? '允许下单未开' : !ts.bridge_online ? '桥接离线' : ''}` : ''}
          >
            {placing ? <Loader2 size={12} className="mx-auto animate-spin" /> : order.side === 'buy' ? '买入下单' : '卖出下单'}
          </button>
        </div>
        {!tradable && (
          <p className="mt-2 text-[11px] text-text-muted">
            当前不可下单：{!ts.trading_enabled ? '总闸关闭' : !ts.allow_order ? '「允许下单」未开' : !ts.bridge_online ? '桥接离线（QMT 桥接策略未运行）' : ''}
          </p>
        )}
      </div>

      {/* L2 信号执行 */}
      <div className="rounded-lg border border-border bg-surface p-4">
        <div className="mb-2 flex items-center justify-between">
          <h4 className="text-xs font-semibold text-text-secondary">L2 · 信号半自动（点「执行」按下方股数转真实订单）</h4>
          <button
            onClick={async () => {
              try {
                const r = await tradingApi.testSignal({ code: '600519.SH', action: 'buy' })
                if (r.ok) {
                  toast.info(
                    `测试信号已注入：${r.signal.code} ${r.signal.action} @${r.signal.price ?? '—'}` +
                    (r.auto_will_execute ? '（全自动运行中——已尝试自动执行！）' : '（全自动关闭，仅供 L2 手动执行）'),
                  )
                } else {
                  toast.error(r.error || '注入失败')
                }
              } catch (e) {
                toast.error(`注入失败：${e}`)
              }
            }}
            className="rounded border border-accent/40 bg-accent/15 px-2 py-1 text-[11px] text-accent hover:bg-accent/25"
            title="注入一条测试信号，走与真实策略完全相同的管道（信号流 → L2/L3）"
          >
            发送测试信号
          </button>
        </div>
        <div className="mb-2 flex items-center gap-2 text-xs">
          <span className="text-text-muted">执行股数：</span>
          <input
            type="number" step={100} value={execVolume}
            onChange={(e) => setExecVolume(e.target.value)}
            className="num w-24 rounded border border-border bg-card px-2 py-1 outline-none"
          />
        </div>
        {signals.filter((s) => s.action === 'buy' || s.action === 'sell').length === 0 ? (
          <p className="text-xs text-text-muted">暂无可执行信号（策略推的 buy/sell 信号会出现在这里）</p>
        ) : (
          <div className="max-h-40 space-y-1 overflow-y-auto">
            {signals.filter((s) => s.action === 'buy' || s.action === 'sell').slice(0, 10).map((s, i) => (
              <div key={i} className="flex items-center gap-2 rounded border border-border/60 bg-card px-2.5 py-1.5 text-xs">
                <span className="text-text-muted">{s.time?.slice(11)}</span>
                <span className="text-text-secondary">{s.strategy}</span>
                <span className="num text-teal">{s.code}</span>
                <span className={s.action === 'buy' ? 'text-loss' : 'text-gain'}>{s.action === 'buy' ? '买入' : '卖出'}</span>
                {s.price != null && <span className="num">{fmt(Number(s.price), 2)}</span>}
                <button
                  onClick={() => setExecSignal({ code: s.code, action: s.action, price: s.price ?? undefined, strategy: s.strategy })}
                  className="ml-auto rounded border border-accent/40 bg-accent/15 px-2 py-0.5 text-accent hover:bg-accent/25"
                >
                  执行
                </button>
              </div>
            ))}
          </div>
        )}
        {execSignal && (
          <div className="mt-2 rounded border border-accent/40 bg-accent/10 p-2.5 text-xs">
            <div className="mb-1.5">
              将执行：<b>{execSignal.action === 'buy' ? '买入' : '卖出'}</b>{' '}
              <span className="num text-teal">{execSignal.code}</span>{' '}
              {execVolume} 股 {execSignal.price ? `@限价 ${execSignal.price}` : '@市价'}
              {execSignal.strategy && ` · 信号来自「${execSignal.strategy}」`}
            </div>
            <div className="flex gap-2">
              <button
                onClick={execOne}
                disabled={executing}
                className="rounded bg-accent px-3 py-1 text-white hover:bg-accent/85 disabled:opacity-50"
              >
                {executing ? <Loader2 size={11} className="mx-auto animate-spin" /> : '确认执行'}
              </button>
              <button onClick={() => setExecSignal(null)} className="rounded border border-border px-3 py-1 text-text-secondary">
                取消
              </button>
            </div>
          </div>
        )}
      </div>

      {/* L3 全自动 + 风控额度 */}
      <div className="rounded-lg border border-border bg-surface p-4">
        <div className="mb-2 flex items-center justify-between">
          <h4 className="text-xs font-semibold text-text-secondary">L3 · 全自动（信号命中规则表即自动下单，无需人工）</h4>
          <button
            onClick={toggleAuto}
            disabled={!ts.allow_order}
            className={`rounded px-3 py-1 text-xs font-semibold disabled:opacity-40 ${
              ts.auto_trading ? 'bg-loss/20 text-loss border border-loss/50' : 'bg-gain/15 text-gain border border-gain/40'
            }`}
            title={!ts.allow_order ? '请先开启「允许下单」' : ''}
          >
            {ts.auto_trading ? '● 全自动运行中 — 点击关闭' : '开启全自动'}
          </button>
        </div>
        {ts.auto_trading && (
          <p className="mb-2 rounded border border-loss/40 bg-loss/10 px-2 py-1.5 text-[11px] text-loss">
            ⚠ 全自动运行中：策略信号命中下方规则即真实下单。熔断阈值 {ts.circuit_loss_pct}%（当日资产回撤触顶自动停机）。
          </p>
        )}
        {/* 规则表 */}
        <div className="space-y-1.5">
          {rules.map((r, i) => (
            <div key={i} className="grid grid-cols-12 gap-1.5 text-xs">
              <input
                value={r.strategy}
                onChange={(e) => setRules((prev) => prev.map((x, j) => (j === i ? { ...x, strategy: e.target.value } : x)))}
                placeholder="策略名(* 通配)"
                className="col-span-4 rounded border border-border bg-card px-2 py-1 outline-none"
              />
              <input
                value={r.code}
                onChange={(e) => setRules((prev) => prev.map((x, j) => (j === i ? { ...x, code: e.target.value.toUpperCase() } : x)))}
                placeholder="代码(* 通配)"
                className="num col-span-3 rounded border border-border bg-card px-2 py-1 outline-none"
              />
              <select
                value={r.side}
                onChange={(e) => setRules((prev) => prev.map((x, j) => (j === i ? { ...x, side: e.target.value } : x)))}
                className="col-span-2 rounded border border-border bg-card px-1 py-1 outline-none"
              >
                <option value="both">双向</option>
                <option value="buy">仅买</option>
                <option value="sell">仅卖</option>
              </select>
              <input
                type="number" step={100} value={r.volume}
                onChange={(e) => setRules((prev) => prev.map((x, j) => (j === i ? { ...x, volume: e.target.value } : x)))}
                placeholder="股数"
                className="num col-span-2 rounded border border-border bg-card px-2 py-1 outline-none"
              />
              <button
                onClick={() => setRules((prev) => prev.filter((_, j) => j !== i))}
                className="col-span-1 rounded border border-border text-text-muted hover:text-loss"
              >
                ×
              </button>
            </div>
          ))}
        </div>
        <button
          onClick={() => setRules((prev) => [...prev, { strategy: '*', code: '*', side: 'both', volume: '100' }])}
          className="mt-1.5 rounded border border-border px-2 py-1 text-[11px] text-text-secondary hover:text-text-primary"
        >
          + 加一条规则
        </button>
        <div className="mt-2 flex items-center gap-2">
          <button onClick={saveRules} className="rounded bg-teal px-3 py-1 text-xs text-white hover:bg-teal/85">
            保存规则表
          </button>
          <span className="text-[11px] text-text-muted">命中第一条规则即执行；规则走同一风控链</span>
        </div>

        {/* 风控额度 */}
        <div className="mt-4 border-t border-border pt-3">
          <h4 className="mb-2 text-xs font-semibold text-text-secondary">风控额度</h4>
          <div className="grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
            <label className="flex flex-col gap-1">
              <span className="text-text-muted">单笔上限(股)</span>
              <input type="number" value={maxVol} onChange={(e) => setMaxVol(e.target.value)} className="num rounded border border-border bg-card px-2 py-1 outline-none" />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-text-muted">每日最多(笔)</span>
              <input type="number" value={maxPerDay} onChange={(e) => setMaxPerDay(e.target.value)} className="num rounded border border-border bg-card px-2 py-1 outline-none" />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-text-muted">熔断回撤(%)</span>
              <input type="number" step={0.5} value={circuit} onChange={(e) => setCircuit(e.target.value)} className="num rounded border border-border bg-card px-2 py-1 outline-none" />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-text-muted">白名单(逗号分隔)</span>
              <input value={allowlistText} onChange={(e) => setAllowlistText(e.target.value)} placeholder="600519.SH,510300.SH" className="num rounded border border-border bg-card px-2 py-1 outline-none" />
            </label>
          </div>
          <button onClick={saveLimits} className="mt-2 rounded border border-teal/40 bg-teal/15 px-3 py-1 text-xs text-teal hover:bg-teal/25">
            保存风控额度
          </button>
        </div>
      </div>

      {/* 审计日志 */}
      <div className="rounded-lg border border-border bg-surface p-4">
        <h4 className="mb-2 text-xs font-semibold text-text-secondary">交易审计（全部留痕，最新在前）</h4>
        <AuditList />
      </div>
    </div>
  )
}

function AuditList() {
  const [items, setItems] = useState<AuditEntry[]>([])
  const load = useCallback(() => {
    tradingApi.audit().then((r) => setItems(r.audit || [])).catch(() => {})
  }, [])
  useEffect(() => {
    load()
    const t = window.setInterval(load, 5000)
    return () => window.clearInterval(t)
  }, [load])
  if (items.length === 0) return <p className="text-xs text-text-muted">暂无审计记录</p>
  return (
    <div className="max-h-52 space-y-1 overflow-y-auto font-mono text-[11px]">
      {items.map((a, i) => (
        <div key={i} className="flex items-start gap-2">
          <span className="shrink-0 text-text-muted">{a.time?.slice(11)}</span>
          <span className={`shrink-0 ${String(a.event).includes('reject') || String(a.event).includes('break') ? 'text-loss' : String(a.event).includes('accepted') ? 'text-gain' : 'text-accent'}`}>
            {String(a.event)}
          </span>
          <span className="truncate text-text-secondary">{JSON.stringify(a)}</span>
        </div>
      ))}
    </div>
  )
}

function Table({ headers, rows }: { headers: string[]; rows: React.ReactNode[][] }) {
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b border-border text-xs text-text-secondary">
          {headers.map((h, i) => (
            <th key={i} className={`px-4 py-2.5 font-normal ${i === 0 ? 'text-left' : 'text-right'}`}>{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => (
          <tr key={i} className="border-b border-border/40 last:border-0">
            {row.map((cell, j) => (
              <td key={j} className={`px-4 py-2.5 ${j === 0 ? 'text-left' : 'text-right'}`}>{cell}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Empty({ text }: { text: string }) {
  return <div className="py-10 text-center text-sm text-text-secondary">{text}</div>
}

function ReportsView() {
  const [reports, setReports] = useState<{ date: string; created?: string }[]>([])
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    bridgeApi
      .reports()
      .then((r) => setReports(r.reports || []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])
  if (loading) return <div className="flex justify-center py-8"><Loader2 size={16} className="animate-spin text-text-muted" /></div>
  if (reports.length === 0) return <Empty text="暂无收盘日报（交易日 15:05 后自动生成）" />
  return (
    <div className="divide-y divide-border/40">
      {reports.map((r) => (
        <div key={r.date} className="flex items-center justify-between px-4 py-2.5 text-sm">
          <span className="num text-teal">{r.date}</span>
          <span className="text-xs text-text-muted">生成于 {String(r.created ?? '').slice(11, 19)}</span>
        </div>
      ))}
    </div>
  )
}
