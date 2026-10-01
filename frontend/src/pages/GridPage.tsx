import { useEffect, useMemo, useState } from 'react'
import { Pause, Play, RefreshCw, Square, Trash2 } from 'lucide-react'
import { gridApi } from '../api/grid'
import { useToast } from '../components/Toast'

type GridJob = Record<string, any>
type Mode = 'percent' | 'fixed' | 'trailing' | 'atr' | 'autot' | 'rolling' | 'td9'

const MODES: { id: Mode; title: string; lines: string[]; isNew?: boolean }[] = [
  { id: 'percent', title: '百分比网格', lines: ['涨/跌固定%触发', '适合高价个股'] },
  { id: 'fixed', title: '固定价差', lines: ['涨/跌固定价差触发', '适合ETF/蓝筹'] },
  { id: 'trailing', title: '追踪模式', lines: ['动态跟随高/低价', '适合趋势行情'] },
  { id: 'atr', title: 'ATR 动态', lines: ['按波动率自适应间距', '适合高波动标的'] },
  { id: 'autot', title: 'AutoT 做T', lines: ['基于支撑/阻力位', '自动做T捕捉差价'], isNew: true },
  { id: 'rolling', title: '区间滚仓', lines: ['全程自动运行', '自动做T/止盈/止损'], isNew: true },
  { id: 'td9', title: '九转做T', lines: ['神奇九转形态信号', '触发买卖做日内差价'] },
]

const MODE_HINT: Record<Mode, string> = {
  percent: '价格每上涨/下跌固定百分比触发一格。',
  fixed: '价格每变化固定价差触发一格。',
  trailing: '追踪阶段高低点，回撤或反弹达到阈值后触发。',
  atr: '按 ATR 波动率自动计算网格间距。',
  autot: '识别近期支撑与阻力，在区间内自动低吸高抛。',
  rolling: '在设定区间内持续滚动，并按止盈止损退出。',
  td9: '出现「下跌9转」买入、「上涨9转」卖出，做日内差价。每笔数量复用「每格数量」。',
}

const INPUT = 'mt-1 h-9 w-full rounded border border-[#28313a] bg-[#0c1117] px-3 text-sm text-slate-200 outline-none placeholder:text-slate-600 focus:border-blue-500/60'
const LABEL = 'text-xs text-slate-300'

export default function GridPage() {
  const toast = useToast()
  const [jobs, setJobs] = useState<GridJob[]>([])
  const [mode, setMode] = useState<Mode>('td9')
  const [saving, setSaving] = useState(false)
  const [form, setForm] = useState({
    symbol: '512880.SH', name: '', basePrice: '', upper: '', lower: '',
    stepPct: '1.5', fixedGap: '0.01', atrPeriod: '14', atrMultiple: '1.0',
    retracePct: '', reboundPct: '', supportPeriod: '20', takeProfitPct: '3', stopLossPct: '2',
    tradeMode: 'instant', orderType: 'opponent', perGridShares: '0',
    lockedShares: '', maxPositionShares: '', insufficientAction: 'continue', gridLevels: '10',
  })

  async function load() {
    try { setJobs(await gridApi.list()) } catch (error) { toast.error(error instanceof Error ? error.message : '网格加载失败') }
  }
  useEffect(() => { load() }, [])

  const modeTitle = useMemo(() => MODES.find((item) => item.id === mode)?.title || '网格', [mode])
  const change = (key: keyof typeof form, value: string) => setForm((old) => ({ ...old, [key]: value }))

  function payload() {
    const shares = Number(form.perGridShares || 0)
    const base = Number(form.basePrice || 0)
    return {
      symbol: form.symbol, name: form.name || `${form.symbol} ${modeTitle}`, mode,
      base_price: base, step_pct: Number(form.stepPct || 1.5) / 100,
      per_grid_amount: shares > 0 ? Math.max(shares * (base || 10), 1) : 0,
      upper_limit: Number(form.upper || 0), lower_limit: Number(form.lower || 0),
      grid_levels: Number(form.gridLevels || 10), spacing_type: mode === 'fixed' ? 'arithmetic' : 'geometric',
      max_position_amount: Number(form.maxPositionShares || 0) * (base || 10),
      config: {
        trade_mode: form.tradeMode, order_type: form.orderType, per_grid_shares: shares,
        locked_shares: Number(form.lockedShares || 0), max_position_shares: Number(form.maxPositionShares || 0),
        insufficient_action: form.insufficientAction,
        sell_retrace_pct: form.retracePct ? Number(form.retracePct) : null,
        buy_rebound_pct: form.reboundPct ? Number(form.reboundPct) : null,
        fixed_gap: Number(form.fixedGap || 0), atr_period: Number(form.atrPeriod || 14),
        atr_multiple: Number(form.atrMultiple || 1), support_period: Number(form.supportPeriod || 20),
        take_profit_pct: Number(form.takeProfitPct || 0), stop_loss_pct: Number(form.stopLossPct || 0),
      },
    }
  }

  async function save(submit = false) {
    if (!/^\d{6}\.(SH|SZ|BJ)$/i.test(form.symbol.trim())) { toast.error('请输入如 512880.SH 的完整证券代码'); return }
    if (submit && (!(Number(form.perGridShares) > 0) || Number(form.perGridShares) % 100 !== 0)) {
      toast.error('提交 QMT 时，每格股数必须是 100 的正整数倍'); return
    }
    setSaving(true)
    try {
      const created = await gridApi.create(payload())
      if (submit) {
        const result = await gridApi.submitQmt(Number(created.id))
        toast.success(result.execution_enabled ? '已保存并提交 QMT，自动执行已启用' : '规则已提交 QMT；交易开关当前未全部开启')
      } else toast.success('网格配置已保存')
      await load()
    } catch (error) { toast.error(error instanceof Error ? error.message : '保存失败') }
    finally { setSaving(false) }
  }

  return (
    <div className="h-full overflow-auto bg-[#10151b] p-4 text-slate-200 lg:p-5">
      <div className="mx-auto max-w-[1180px] overflow-hidden rounded-lg border border-[#242c34] bg-[#151a20] shadow-2xl">
        <div className="p-4">
          <div className="mb-2 text-sm font-medium text-slate-300">网格模式</div>
          <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-3">
            {MODES.map((item) => (
              <button key={item.id} type="button" onClick={() => setMode(item.id)}
                className={`min-h-[70px] rounded-lg border px-3 py-2 text-center transition ${mode === item.id ? 'border-blue-500 bg-blue-500/10 shadow-[0_0_0_1px_rgba(59,130,246,.2)]' : 'border-[#2a323b] bg-[#171d23] hover:border-slate-500'}`}>
                <div className={`text-sm font-semibold ${mode === item.id ? 'text-blue-300' : 'text-slate-200'}`}>
                  {item.title} {item.isNew && <span className="ml-1 rounded bg-amber-400 px-1 py-0.5 text-[10px] font-bold text-black">NEW</span>}
                </div>
                {item.lines.map((line) => <div key={line} className="text-xs leading-5 text-slate-400">{line}</div>)}
              </button>
            ))}
          </div>
        </div>

        <Section title={`${modeTitle} 参数`}>
          <div className="mb-4 rounded border border-emerald-600/30 bg-emerald-950/25 px-3 py-2 text-xs text-emerald-400">✓ {modeTitle}已就绪：{MODE_HINT[mode]}</div>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Field label="标的代码"><input className={INPUT} value={form.symbol} onChange={(e) => change('symbol', e.target.value.toUpperCase())} /></Field>
            <Field label="任务名称"><input className={INPUT} value={form.name} onChange={(e) => change('name', e.target.value)} placeholder={`${form.symbol} ${modeTitle}`} /></Field>
            {(mode === 'td9' || mode === 'trailing') && <>
              <Field label="卖出回落 %"><input className={INPUT} value={form.retracePct} onChange={(e) => change('retracePct', e.target.value)} placeholder="留空=不追踪" /></Field>
              <Field label="买入反弹 %"><input className={INPUT} value={form.reboundPct} onChange={(e) => change('reboundPct', e.target.value)} placeholder="留空=不追踪" /></Field>
            </>}
            {mode === 'percent' && <Field label="每格涨跌 %"><input className={INPUT} value={form.stepPct} onChange={(e) => change('stepPct', e.target.value)} /></Field>}
            {mode === 'fixed' && <Field label="固定价差"><input className={INPUT} value={form.fixedGap} onChange={(e) => change('fixedGap', e.target.value)} /></Field>}
            {mode === 'atr' && <><Field label="ATR 周期"><input className={INPUT} value={form.atrPeriod} onChange={(e) => change('atrPeriod', e.target.value)} /></Field><Field label="ATR 倍数"><input className={INPUT} value={form.atrMultiple} onChange={(e) => change('atrMultiple', e.target.value)} /></Field></>}
            {mode === 'autot' && <Field label="支撑/阻力周期"><input className={INPUT} value={form.supportPeriod} onChange={(e) => change('supportPeriod', e.target.value)} /></Field>}
            {mode === 'rolling' && <><Field label="止盈 %"><input className={INPUT} value={form.takeProfitPct} onChange={(e) => change('takeProfitPct', e.target.value)} /></Field><Field label="止损 %"><input className={INPUT} value={form.stopLossPct} onChange={(e) => change('stopLossPct', e.target.value)} /></Field></>}
            <Field label="区间下限"><input className={INPUT} value={form.lower} onChange={(e) => change('lower', e.target.value)} placeholder="留空=自动计算" /></Field>
            <Field label="区间上限"><input className={INPUT} value={form.upper} onChange={(e) => change('upper', e.target.value)} placeholder="留空=自动计算" /></Field>
          </div>
        </Section>

        <Section title="下单设置">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Field label="交易模式"><select className={INPUT} value={form.tradeMode} onChange={(e) => change('tradeMode', e.target.value)}><option value="instant">即时成交</option><option value="signal">信号确认</option></select></Field>
            <Field label="委托类型"><select className={INPUT} value={form.orderType} onChange={(e) => change('orderType', e.target.value)}><option value="opponent">对手价</option><option value="latest">最新价</option><option value="limit">限价</option></select></Field>
          </div>
          <div className="mt-4 max-w-[350px]"><Field label="每格股数"><input className={INPUT} type="number" step="100" min="0" value={form.perGridShares} onChange={(e) => change('perGridShares', e.target.value)} /></Field></div>
        </Section>

        <Section title="仓位风控">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-[1fr_1fr_2fr]">
            <Field label="锁定不卖（股）"><input className={INPUT} value={form.lockedShares} onChange={(e) => change('lockedShares', e.target.value)} placeholder="不限制" /></Field>
            <Field label="最大持仓（股）"><input className={INPUT} value={form.maxPositionShares} onChange={(e) => change('maxPositionShares', e.target.value)} placeholder="不限制" /></Field>
            <Field label="资金/持仓不足时"><select className={INPUT} value={form.insufficientAction} onChange={(e) => change('insufficientAction', e.target.value)}><option value="continue">继续运行（卖出回血自动恢复）</option><option value="pause">暂停并等待人工处理</option><option value="stop">停止策略</option></select></Field>
          </div>
        </Section>

        <div className="flex justify-end gap-2 border-t border-[#242c34] bg-[#12171d] px-4 py-3">
          <button type="button" onClick={() => setMode('td9')} className="rounded-lg border border-[#303945] bg-[#1a2028] px-5 py-2 text-sm text-slate-300 hover:bg-[#222a34]">取消</button>
          <button type="button" disabled={saving} onClick={() => save(false)} className="rounded-lg border border-blue-500/40 bg-blue-500/10 px-5 py-2 text-sm text-blue-300 disabled:opacity-50">仅保存配置</button>
          <button type="button" disabled={saving} onClick={() => save(true)} className="rounded-lg bg-blue-600 px-5 py-2 text-sm font-semibold text-white shadow-lg shadow-blue-900/30 hover:bg-blue-500 disabled:opacity-50">{saving ? '正在保存…' : '保存并提交 QMT'}</button>
        </div>
      </div>
      <JobList jobs={jobs} reload={load} toast={toast} />
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) { return <section className="border-t border-[#242c34]"><div className="border-b border-[#242c34] bg-[#191f25] px-4 py-2 text-sm font-semibold text-slate-300">{title}</div><div className="p-4">{children}</div></section> }
function Field({ label, children }: { label: string; children: React.ReactNode }) { return <label className={LABEL}>{label}{children}</label> }

function JobList({ jobs, reload, toast }: { jobs: GridJob[]; reload: () => Promise<void>; toast: ReturnType<typeof useToast> }) {
  async function action(kind: 'start' | 'pause' | 'stop', id: number) { try { await gridApi[kind](id); await reload() } catch (error) { toast.error(error instanceof Error ? error.message : '操作失败') } }
  async function remove(id: number) { if (!window.confirm('确定删除这个网格任务及成交记录吗？')) return; try { await gridApi.remove(id); await reload() } catch (error) { toast.error(error instanceof Error ? error.message : '删除失败') } }
  return <div className="mx-auto mt-4 max-w-[1180px] rounded-lg border border-[#242c34] bg-[#151a20] p-4">
    <div className="mb-3 flex items-center justify-between"><span className="text-sm font-semibold">已保存网格</span><button onClick={reload} className="flex items-center gap-1 text-xs text-slate-400 hover:text-white"><RefreshCw size={13}/>刷新</button></div>
    {jobs.length === 0 ? <div className="py-6 text-center text-xs text-slate-500">暂无已保存网格</div> : <div className="overflow-x-auto"><table className="w-full text-left text-xs"><thead className="text-slate-500"><tr><th className="py-2">名称</th><th>标的</th><th>模式</th><th>状态</th><th>每格股数</th><th>QMT</th><th className="text-right">操作</th></tr></thead><tbody>
      {jobs.map((job) => <tr key={job.id} className="border-t border-[#242c34] text-slate-300"><td className="py-2.5 pr-3">{job.name}</td><td>{job.symbol}</td><td>{MODES.find((m) => m.id === job.mode)?.title || '百分比网格'}</td><td>{statusName(job.status)}</td><td>{job.config?.per_grid_shares || 0}</td><td className={job.submitted_to_qmt ? 'text-emerald-400' : 'text-slate-500'}>{job.submitted_to_qmt ? '已提交' : '未提交'}</td><td><div className="flex justify-end gap-1">{job.status !== 'running' && <IconButton title="启动" onClick={() => action('start', job.id)}><Play size={13}/></IconButton>}{job.status === 'running' && <IconButton title="暂停" onClick={() => action('pause', job.id)}><Pause size={13}/></IconButton>}<IconButton title="停止" onClick={() => action('stop', job.id)}><Square size={13}/></IconButton><IconButton title="删除" onClick={() => remove(job.id)}><Trash2 size={13}/></IconButton></div></td></tr>)}
    </tbody></table></div>}
  </div>
}

function IconButton({ title, onClick, children }: { title: string; onClick: () => void; children: React.ReactNode }) { return <button title={title} onClick={onClick} className="rounded border border-[#303945] p-1.5 text-slate-400 hover:bg-slate-800 hover:text-white">{children}</button> }
function statusName(value: string) { return ({ idle: '未启动', running: '运行中', paused: '已暂停', stopped: '已停止' } as Record<string, string>)[value] || value }
