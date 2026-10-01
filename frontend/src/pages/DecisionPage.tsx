import {
  Brain,
  Loader2,
  Plus,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Trash2,
  TrendingDown,
  TrendingUp,
} from 'lucide-react'
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { useState } from 'react'
import { decisionApi, type DecisionResult, type Evidence } from '../lib/api'
import { fmtPct, pnlColor } from '../lib/utils'
import { useToast } from '../components/Toast'

interface EvidenceForm extends Evidence {
  _id: number
}

const SAMPLE = {
  hypothesis: '未来一个月贵州茅台跑赢沪深300',
  timeframe: '未来一个月',
  success_criteria: '区间收益率之差 > 0',
  prior: 0.5,
  evidences: [
    { name: '白酒动销数据回暖', direction: 'support' as const, quality: 'strong' as const },
    { name: '北向资金近期流出', direction: 'against' as const, quality: 'medium' as const },
    { name: '多位分析师上调目标价', direction: 'support' as const, quality: 'weak' as const },
    { name: '估值仍高于历史中枢', direction: 'against' as const, quality: 'medium' as const },
  ],
}

export default function DecisionPage() {
  const toast = useToast()
  const [hypothesis, setHypothesis] = useState('')
  const [timeframe, setTimeframe] = useState('')
  const [criteria, setCriteria] = useState('')
  const [prior, setPrior] = useState(0.5)
  const [evidences, setEvidences] = useState<EvidenceForm[]>([
    { _id: 1, name: '', direction: 'support', quality: 'medium', note: '' },
  ])
  const [result, setResult] = useState<DecisionResult | null>(null)
  const [running, setRunning] = useState(false)

  const loadSample = () => {
    setHypothesis(SAMPLE.hypothesis)
    setTimeframe(SAMPLE.timeframe)
    setCriteria(SAMPLE.success_criteria)
    setPrior(SAMPLE.prior)
    setEvidences(
      SAMPLE.evidences.map((e, i) => ({ ...e, note: '', _id: Date.now() + i })),
    )
  }

  const run = async () => {
    const valid = evidences.filter((e) => e.name.trim())
    if (!hypothesis.trim()) {
      toast.error('请先填写决策假设')
      return
    }
    if (valid.length === 0) {
      toast.error('至少需要一条证据')
      return
    }
    setRunning(true)
    try {
      const { result: r } = await decisionApi.analyze({
        hypothesis: hypothesis.trim(),
        prior,
        timeframe: timeframe.trim(),
        success_criteria: criteria.trim(),
        evidences: valid.map(({ name, direction, quality, note }) => ({
          name: name.trim(),
          direction,
          quality,
          note,
        })),
      })
      setResult(r)
    } catch (e) {
      toast.error(`分析失败：${e}`)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="flex h-full overflow-hidden">
      {/* 左：输入面板 */}
      <div className="w-[380px] shrink-0 overflow-y-auto border-r border-border bg-surface/40 p-5">
        <div className="space-y-4">
          <div>
            <label className="text-xs text-text-secondary">决策假设（必填）</label>
            <textarea
              value={hypothesis}
              onChange={(e) => setHypothesis(e.target.value)}
              placeholder="如：未来一个月贵州茅台跑赢沪深300"
              rows={3}
              className="mt-1.5 w-full resize-none rounded-lg border border-border bg-card px-3 py-2 text-sm outline-none focus:border-accent/50"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-text-secondary">时间范围</label>
              <input
                value={timeframe}
                onChange={(e) => setTimeframe(e.target.value)}
                placeholder="未来一个月"
                className="mt-1.5 w-full rounded-lg border border-border bg-card px-3 py-2 text-sm outline-none focus:border-accent/50"
              />
            </div>
            <div>
              <label className="text-xs text-text-secondary">成功标准</label>
              <input
                value={criteria}
                onChange={(e) => setCriteria(e.target.value)}
                placeholder="超额收益 > 0"
                className="mt-1.5 w-full rounded-lg border border-border bg-card px-3 py-2 text-sm outline-none focus:border-accent/50"
              />
            </div>
          </div>

          <div>
            <div className="flex items-center justify-between">
              <label className="text-xs text-text-secondary">先验概率</label>
              <span className="num text-sm text-accent">{(prior * 100).toFixed(0)}%</span>
            </div>
            <input
              type="range"
              min={5}
              max={95}
              step={5}
              value={prior * 100}
              onChange={(e) => setPrior(Number(e.target.value) / 100)}
              className="mt-2 w-full accent-accent"
            />
            <div className="mt-1 flex justify-between text-[10px] text-text-muted">
              <span>很可能不成立</span>
              <span>没把握</span>
              <span>很可能成立</span>
            </div>
          </div>

          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <label className="text-xs text-text-secondary">证据列表</label>
              <button
                onClick={() =>
                  setEvidences((prev) => [
                    ...prev,
                    {
                      _id: Date.now(),
                      name: '',
                      direction: 'support',
                      quality: 'medium',
                      note: '',
                    },
                  ])
                }
                className="flex items-center gap-1 text-xs text-accent hover:text-accent/80"
              >
                <Plus size={13} /> 加一条
              </button>
            </div>
            <div className="space-y-2">
              {evidences.map((ev, idx) => (
                <div key={ev._id} className="rounded-lg border border-border bg-card p-2.5">
                  <div className="flex items-center gap-2">
                    <input
                      value={ev.name}
                      onChange={(e) =>
                        setEvidences((prev) =>
                          prev.map((x, i) =>
                            i === idx ? { ...x, name: e.target.value } : x,
                          ),
                        )
                      }
                      placeholder="证据描述"
                      className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-text-muted"
                    />
                    {evidences.length > 1 && (
                      <button
                        onClick={() =>
                          setEvidences((prev) => prev.filter((_, i) => i !== idx))
                        }
                        className="shrink-0 text-text-muted hover:text-loss"
                      >
                        <Trash2 size={13} />
                      </button>
                    )}
                  </div>
                  <div className="mt-2 grid grid-cols-2 gap-2">
                    <select
                      value={ev.direction}
                      onChange={(e) =>
                        setEvidences((prev) =>
                          prev.map((x, i) =>
                            i === idx
                              ? { ...x, direction: e.target.value as Evidence['direction'] }
                              : x,
                          ),
                        )
                      }
                      className="rounded border border-border bg-surface px-2 py-1.5 text-xs outline-none"
                    >
                      <option value="support">✅ 支持假设</option>
                      <option value="against">❌ 反对假设</option>
                    </select>
                    <select
                      value={ev.quality}
                      onChange={(e) =>
                        setEvidences((prev) =>
                          prev.map((x, i) =>
                            i === idx
                              ? { ...x, quality: e.target.value as Evidence['quality'] }
                              : x,
                          ),
                        )
                      }
                      className="rounded border border-border bg-surface px-2 py-1.5 text-xs outline-none"
                    >
                      <option value="strong">强 · 一手硬数据</option>
                      <option value="medium">中 · 间接证据</option>
                      <option value="weak">弱 · 传闻情绪</option>
                    </select>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="flex gap-2">
            <button
              onClick={loadSample}
              className="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-border bg-card py-2.5 text-sm text-text-secondary hover:text-text-primary"
            >
              <Sparkles size={14} /> 载入示例
            </button>
            <button
              onClick={run}
              disabled={running}
              className="flex flex-[2] items-center justify-center gap-1.5 rounded-lg bg-accent py-2.5 text-sm font-medium text-white hover:bg-accent/85 disabled:opacity-50"
            >
              {running ? (
                <Loader2 size={15} className="animate-spin" />
              ) : (
                <Brain size={15} />
              )}
              {running ? '分析中…' : '运行贝叶斯分析'}
            </button>
          </div>
        </div>
      </div>

      {/* 右：结果面板 */}
      <div className="min-w-0 flex-1 overflow-y-auto p-5">
        {result ? (
          <DecisionResultView r={result} />
        ) : (
          <div className="flex h-full flex-col items-center justify-center text-center">
            <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-accent/40 bg-accent/15">
              <Brain size={26} className="text-accent" />
            </div>
            <h3 className="mt-4 font-medium">可追溯、可复现的概率推理</h3>
            <p className="mt-1 max-w-md text-sm leading-6 text-text-secondary">
              设定先验 → 证据分级（强/中/弱）→ 对数几率逐条更新 →
              期望值 EV 行动对比 → 敏感性分析。
              全部纯 Python 计算零依赖，不是大模型编的。
            </p>
          </div>
        )}
      </div>
    </div>
  )
}

function DecisionResultView({ r }: { r: DecisionResult }) {
  const traceData = r.belief_trace.map((b, i) => ({
    idx: i,
    label: b.evidence
      ? b.evidence.length > 8
        ? `${b.evidence.slice(0, 8)}…`
        : b.evidence
      : '先验',
    posterior: +(b.posterior * 100).toFixed(1),
  }))

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      {/* 推荐卡 */}
      <div className="rounded-xl border border-accent/30 bg-gradient-to-br from-accent/15 to-transparent p-5">
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <div>
            <div className="text-xs text-text-secondary">推荐行动</div>
            <div className="mt-1 text-2xl font-semibold text-accent">
              {r.recommendation.action}
            </div>
          </div>
          <div>
            <div className="text-xs text-text-secondary">先验 → 后验</div>
            <div className="num mt-1 text-xl">
              {(r.prior * 100).toFixed(0)}% <span className="text-text-muted">→</span>{' '}
              <span className="text-accent">{(r.posterior * 100).toFixed(1)}%</span>
            </div>
          </div>
          <div>
            <div className="text-xs text-text-secondary">期望收益 EV</div>
            <div className={`num mt-1 text-xl ${pnlColor(r.recommendation.expected_value)}`}>
              {fmtPct(r.recommendation.expected_value)}
            </div>
          </div>
          <div>
            <div className="text-xs text-text-secondary">置信度</div>
            <div className="mt-1 text-xl">{r.recommendation.confidence}</div>
          </div>
        </div>
      </div>

      {/* 信念轨迹 */}
      <div className="rounded-xl border border-border bg-card p-4">
        <h3 className="mb-2 text-sm font-medium">信念更新轨迹</h3>
        <div style={{ height: 220 }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={traceData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e2d47" />
              <XAxis
                dataKey="label"
                tick={{ fill: '#475569', fontSize: 10 }}
                interval={0}
                angle={-15}
                textAnchor="end"
                height={50}
                tickLine={false}
              />
              <YAxis
                domain={[0, 100]}
                tickFormatter={(v: number) => `${v}%`}
                tick={{ fill: '#475569', fontSize: 10 }}
                tickLine={false}
              />
              <Tooltip
                contentStyle={{
                  background: '#111e33',
                  border: '1px solid #1e2d47',
                  borderRadius: 8,
                  fontSize: 12,
                }}
                formatter={(v: number) => [`${v}%`, '后验概率']}
              />
              <ReferenceLine y={50} stroke="#475569" strokeDasharray="4 4" />
              <Line
                type="monotone"
                dataKey="posterior"
                stroke="#6366f1"
                strokeWidth={2}
                dot={{ r: 3, fill: '#6366f1' }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* EV 对比 */}
      <div className="rounded-xl border border-border bg-card p-4">
        <h3 className="mb-3 text-sm font-medium">行动期望值对比</h3>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-text-secondary">
              <th className="pb-2 text-left font-normal">行动</th>
              <th className="pb-2 text-right font-normal">假设成立</th>
              <th className="pb-2 text-right font-normal">假设不成立</th>
              <th className="pb-2 text-right font-normal">期望值 EV</th>
            </tr>
          </thead>
          <tbody>
            {r.actions.map((a, i) => (
              <tr
                key={a.name}
                className={`border-t border-border/60 ${i === 0 ? 'bg-accent/10' : ''}`}
              >
                <td className="py-2.5">
                  {i === 0 && <span className="mr-1 text-accent">★</span>}
                  {a.name}
                </td>
                <td className={`num py-2.5 text-right ${pnlColor(a.payoff_if_true)}`}>
                  {fmtPct(a.payoff_if_true)}
                </td>
                <td className={`num py-2.5 text-right ${pnlColor(a.payoff_if_false)}`}>
                  {fmtPct(a.payoff_if_false)}
                </td>
                <td className={`num py-2.5 text-right font-medium ${pnlColor(a.expected_value)}`}>
                  {fmtPct(a.expected_value)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 敏感性分析 */}
      <div
        className={`rounded-xl border p-4 ${
          r.sensitivity.robust
            ? 'border-gain/30 bg-gain/5'
            : 'border-warn/30 bg-warn/5'
        }`}
      >
        <div className="flex items-center gap-2">
          {r.sensitivity.robust ? (
            <ShieldCheck size={16} className="text-gain" />
          ) : (
            <ShieldAlert size={16} className="text-warn" />
          )}
          <h3 className="text-sm font-medium">敏感性分析</h3>
        </div>
        <p className="mt-2 text-sm leading-6 text-text-secondary">{r.sensitivity.note}</p>
        {r.sensitivity.critical_evidence.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {r.sensitivity.critical_evidence.map((c) => (
              <span
                key={c}
                className="rounded border border-warn/40 bg-warn/10 px-2 py-0.5 text-xs text-warn"
              >
                关键证据：{c}
              </span>
            ))}
          </div>
        )}
        {r.fragile_evidence.length > 0 && (
          <div className="mt-3 space-y-1">
            <div className="text-xs text-text-muted">最脆弱的证据支点：</div>
            {r.fragile_evidence.map((f) => (
              <div key={f.evidence} className="flex items-center gap-1.5 text-xs text-text-secondary">
                {f.lr >= 1 ? (
                  <TrendingUp size={12} className="text-gain" />
                ) : (
                  <TrendingDown size={12} className="text-loss" />
                )}
                {f.evidence}
                <span className="text-text-muted">
                  （{f.quality} · LR={f.lr}）
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      <p className="pb-2 text-center text-[11px] text-text-muted">{r.disclaimer}</p>
    </div>
  )
}
