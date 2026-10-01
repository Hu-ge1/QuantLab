import {
  CheckCircle2,
  FileCode2,
  History,
  Loader2,
  Save,
  Sparkles,
  Wand2,
  XCircle,
} from 'lucide-react'
import Editor from '@monaco-editor/react'
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  studioApi,
  type StudioHistoryItem,
  type StudioTemplate,
} from '../lib/api'
import { useToast } from '../components/Toast'

const FORMS = [
  { id: 'qmt_builtin', label: '大QMT内置策略', desc: 'init/handlebar，粘贴进客户端即可运行' },
  { id: 'miniqmt', label: 'MiniQMT 外部脚本', desc: 'xtquant 独立进程' },
]
const MODES = [
  { id: 'backtest', label: '回测优先', desc: '实盘开关默认关闭' },
  { id: 'signal', label: '仅信号', desc: '绝不调用下单函数' },
  { id: 'live', label: '实盘就绪', desc: '带保险校验' },
]

export default function StudioPage() {
  const toast = useToast()
  const [form, setForm] = useState('qmt_builtin')
  const [mode, setMode] = useState('backtest')
  const [requirement, setRequirement] = useState('')
  const [extra, setExtra] = useState('')
  const [templates, setTemplates] = useState<StudioTemplate[]>([])
  const [knowledge, setKnowledge] = useState<{ name: string; size: number }[]>([])
  const [pickedDocs, setPickedDocs] = useState<string[]>([])
  const [generating, setGenerating] = useState(false)
  const [output, setOutput] = useState('')
  const [genError, setGenError] = useState('')
  const [issues, setIssues] = useState<string[] | null>(null)
  const [repairing, setRepairing] = useState(false)
  const [prevCode, setPrevCode] = useState('')
  const [diffText, setDiffText] = useState('')
  const [filename, setFilename] = useState('')
  const [saving, setSaving] = useState(false)
  const [history, setHistory] = useState<StudioHistoryItem[]>([])
  const [showHistory, setShowHistory] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const outputRef = useRef('')

  const loadSide = useCallback(async () => {
    try {
      const [t, k, h] = await Promise.all([
        studioApi.templates(),
        studioApi.knowledge(),
        studioApi.history(),
      ])
      setTemplates(t.templates || [])
      setKnowledge(k.docs || [])
      setHistory(h.items || [])
    } catch {
      /* 静默 */
    }
  }, [])

  useEffect(() => {
    loadSide()
  }, [loadSide])

  useEffect(() => {
    return () => {
      abortRef.current?.abort()
    }
  }, [])

  const generate = async () => {
    if (!requirement.trim()) return toast.error('请先填写策略需求')
    setGenerating(true)
    setGenError('')
    setIssues(null)
    setDiffText('')
    setPrevCode(output)
    setOutput('')
    outputRef.current = ''
    const ab = new AbortController()
    abortRef.current = ab
    try {
      const res = await fetch('/api/studio/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          form, mode, extra, knowledge: pickedDocs,
          requirement, revision: false,
        }),
        signal: ab.signal,
      })
      const reader = res.body!.getReader()
      const dec = new TextDecoder()
      let buf = ''
      let reconnectAvailable = false
      // 上游是 OpenAI 格式 SSE：解析 delta.content 拼接全文
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop() || ''
        for (const line of lines) {
          const s = line.trim()
          if (!s.startsWith('data:')) continue
          const payload = s.slice(5).trim()
          if (payload === '[DONE]') continue
          try {
            const ev = JSON.parse(payload)
            if (ev.error) {
              setGenError(String(ev.error))
              continue
            }
            if (ev.task_id) {
              reconnectAvailable = true
              continue
            }
            const piece = ev.choices?.[0]?.delta?.content
            if (piece) {
              outputRef.current += piece
              setOutput(outputRef.current)
            }
          } catch {
            /* 非 JSON 行跳过 */
          }
        }
      }
      if (outputRef.current.trim()) {
        await studioApi.historyAdd({
          requirement, form, mode, output: outputRef.current,
          title: requirement.slice(0, 40),
        }).catch(() => {})
        loadSide()
      }
      if (reconnectAvailable && !outputRef.current.trim()) {
        setGenError('生成已中断，但后台可能仍在缓冲，可从历史记录查看最新结果')
      }
    } catch (e) {
      if ((e as Error).name !== 'AbortError') setGenError(String(e))
    } finally {
      setGenerating(false)
    }
  }

  const runCheck = async () => {
    if (!output.trim()) return toast.error('还没有生成代码')
    const { issues: r } = await studioApi.check(output, form)
    setIssues(r)
    toast[r.length === 0 ? 'success' : 'info'](r.length === 0 ? '质检通过，没有发现问题' : `发现 ${r.length} 个问题`)
  }

  const runRepair = async () => {
    if (!output.trim()) return toast.error('还没有生成代码')
    setRepairing(true)
    try {
      const r = await studioApi.repair(output, form)
      if (r.ok) {
        setPrevCode(output)
        setOutput(r.code)
        setIssues(r.remaining_issues)
        toast.success(r.remaining_issues.length === 0 ? '修复完成，质检通过' : `修复完成，仍剩 ${r.remaining_issues.length} 个问题`)
      }
    } catch (e) {
      toast.error(`修复失败：${e}`)
    } finally {
      setRepairing(false)
    }
  }

  const runDiff = async () => {
    if (!prevCode.trim()) return toast.error('没有旧版本可对比（修复/二次生成后才有）')
    const { diff } = await studioApi.diff(prevCode, output)
    setDiffText(diff)
  }

  const saveToQmt = async () => {
    if (!output.trim()) return toast.error('还没有生成代码')
    if (!filename.trim()) return toast.error('请填写策略文件名')
    setSaving(true)
    try {
      const r = await studioApi.save({ filename: filename.trim(), code: output, overwrite: false })
      if (r.ok) {
        toast.success(`已保存到 ${r.path}，模型注册${r.registered ? '成功' : '失败：' + r.register_msg}。在 QMT「我的策略」中刷新可见`)
      } else if (r.exists) {
        if (window.confirm(`文件已存在：${filename}。覆盖？（旧版自动备份）`)) {
          const r2 = await studioApi.save({ filename: filename.trim(), code: output, overwrite: true })
          if (r2.ok) toast.success(`已覆盖保存，旧版备份于 ${r2.backup}`)
        }
      } else {
        toast.error(r.error || '保存失败')
      }
    } catch (e) {
      toast.error(`保存失败：${e}`)
    } finally {
      setSaving(false)
    }
  }

  const loadTemplate = (t: StudioTemplate) => {
    setRequirement(String(t.requirement || t.desc || ''))
    setForm(String(t.form || form))
    setMode(String(t.mode || mode))
  }

  const loadHistoryItem = async (id: string) => {
    try {
      const d = await studioApi.historyGet(id)
      setOutput(d.output)
      setRequirement(d.requirement)
      setForm(d.form || form)
      setMode(d.mode || mode)
      setPrevCode('')
      setIssues(null)
      setDiffText('')
      setShowHistory(false)
    } catch (e) {
      toast.error(`加载历史失败：${e}`)
    }
  }

  return (
    <div className="flex h-full overflow-hidden">
      {/* 左：输入 */}
      <div className="w-[360px] shrink-0 overflow-y-auto border-r border-border bg-surface/40 p-4">
        <div className="space-y-3">
          <div>
            <label className="text-xs text-text-secondary">目标形态</label>
            <div className="mt-1 grid grid-cols-1 gap-1.5">
              {FORMS.map((f) => (
                <button
                  key={f.id}
                  onClick={() => setForm(f.id)}
                  className={`rounded-lg border px-3 py-2 text-left text-xs ${form === f.id ? 'border-accent/50 bg-accent/15 text-accent' : 'border-border bg-card text-text-secondary hover:text-text-primary'}`}
                >
                  <div className="font-medium">{f.label}</div>
                  <div className="mt-0.5 text-[11px] opacity-70">{f.desc}</div>
                </button>
              ))}
            </div>
          </div>
          <div>
            <label className="text-xs text-text-secondary">运行模式</label>
            <div className="mt-1 grid grid-cols-3 gap-1.5">
              {MODES.map((m) => (
                <button
                  key={m.id}
                  onClick={() => setMode(m.id)}
                  title={m.desc}
                  className={`rounded-lg border px-2 py-2 text-xs ${mode === m.id ? 'border-accent/50 bg-accent/15 text-accent' : 'border-border bg-card text-text-secondary hover:text-text-primary'}`}
                >
                  {m.label}
                </button>
              ))}
            </div>
          </div>
          <div>
            <label className="text-xs text-text-secondary">策略需求（必填）</label>
            <textarea
              value={requirement}
              onChange={(e) => setRequirement(e.target.value)}
              rows={6}
              placeholder="如：双均线金叉死叉策略，5日线上穿20日线买入、下穿卖出，沪深300成分股，等权持仓5只"
              className="mt-1 w-full resize-none rounded-lg border border-border bg-card px-3 py-2 text-sm outline-none focus:border-accent/50"
            />
          </div>
          <div>
            <label className="text-xs text-text-secondary">补充要求（可选）</label>
            <textarea
              value={extra}
              onChange={(e) => setExtra(e.target.value)}
              rows={3}
              placeholder="如：加入止损 5%、排除 ST"
              className="mt-1 w-full resize-none rounded-lg border border-border bg-card px-3 py-2 text-sm outline-none focus:border-accent/50"
            />
          </div>
          {knowledge.length > 0 && (
            <div>
              <label className="text-xs text-text-secondary">知识库参考</label>
              <div className="mt-1 max-h-32 space-y-1 overflow-y-auto">
                {knowledge.map((d) => (
                  <label key={d.name} className="flex cursor-pointer items-center gap-2 rounded border border-border bg-card px-2 py-1.5 text-xs text-text-secondary">
                    <input
                      type="checkbox"
                      checked={pickedDocs.includes(d.name)}
                      onChange={(e) =>
                        setPickedDocs((prev) =>
                          e.target.checked ? [...prev, d.name] : prev.filter((x) => x !== d.name),
                        )
                      }
                      className="accent-accent"
                    />
                    <span className="truncate">{d.name}</span>
                  </label>
                ))}
              </div>
            </div>
          )}
          <button
            onClick={generate}
            disabled={generating}
            className="flex w-full items-center justify-center gap-1.5 rounded-lg bg-accent py-2.5 text-sm font-medium text-white hover:bg-accent/85 disabled:opacity-50"
          >
            {generating ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}
            {generating ? '生成中…（可点停止）' : 'AI 生成策略'}
          </button>
          {generating && (
            <button
              onClick={() => abortRef.current?.abort()}
              className="w-full rounded-lg border border-loss/40 bg-loss/10 py-2 text-xs text-loss"
            >
              停止生成
            </button>
          )}
          {templates.length > 0 && (
            <div>
              <label className="text-xs text-text-secondary">模板库（点击填入需求）</label>
              <div className="mt-1 max-h-48 space-y-1 overflow-y-auto">
                {templates.map((t, i) => (
                  <button
                    key={i}
                    onClick={() => loadTemplate(t)}
                    className="w-full rounded-lg border border-border bg-card px-2.5 py-2 text-left text-xs hover:border-accent/40"
                  >
                    <div className="font-medium text-text-primary">{String(t.name ?? t.id ?? '模板')}</div>
                    <div className="mt-0.5 line-clamp-2 text-[11px] text-text-muted">{String(t.desc ?? '')}</div>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 右：输出 */}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center justify-between border-b border-border bg-surface/40 px-4 py-2.5">
          <div className="flex items-center gap-2 text-sm">
            <FileCode2 size={15} className="text-teal" />
            <span>策略代码</span>
            {generating && <Loader2 size={13} className="animate-spin text-text-muted" />}
          </div>
          <div className="flex items-center gap-2 text-xs">
            <button onClick={() => setShowHistory(!showHistory)} className="flex items-center gap-1 rounded-lg border border-border px-2.5 py-1.5 text-text-secondary hover:text-text-primary">
              <History size={12} /> 历史
            </button>
            <button onClick={runCheck} disabled={!output} className="rounded-lg border border-border px-2.5 py-1.5 text-text-secondary hover:text-text-primary disabled:opacity-40">
              质检
            </button>
            <button onClick={runRepair} disabled={!output || repairing} className="flex items-center gap-1 rounded-lg border border-border px-2.5 py-1.5 text-text-secondary hover:text-text-primary disabled:opacity-40">
              {repairing ? <Loader2 size={12} className="animate-spin" /> : <Wand2 size={12} />} 自动修复
            </button>
            <button onClick={runDiff} disabled={!output || !prevCode} className="rounded-lg border border-border px-2.5 py-1.5 text-text-secondary hover:text-text-primary disabled:opacity-40">
              Diff
            </button>
            <input
              value={filename}
              onChange={(e) => setFilename(e.target.value)}
              placeholder="策略文件名"
              className="num w-32 rounded-lg border border-border bg-card px-2 py-1.5 outline-none focus:border-teal/50"
            />
            <button onClick={saveToQmt} disabled={!output || saving} className="flex items-center gap-1 rounded-lg bg-teal px-3 py-1.5 text-white hover:bg-teal/85 disabled:opacity-50">
              {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />} 保存到 QMT
            </button>
          </div>
        </div>

        {issues !== null && (
          <div className={`border-b px-4 py-2 text-xs ${issues.length === 0 ? 'border-gain/30 bg-gain/5 text-gain' : 'border-warn/30 bg-warn/5 text-warn'}`}>
            {issues.length === 0 ? (
              <span className="flex items-center gap-1"><CheckCircle2 size={12} /> 质检通过：语法 + QMT Python 3.6 兼容性均无问题</span>
            ) : (
              <div className="flex items-start gap-1"><XCircle size={12} className="mt-0.5 shrink-0" />
                <ul className="list-disc space-y-0.5 pl-3">{issues.map((s, i) => <li key={i}>{s}</li>)}</ul>
              </div>
            )}
          </div>
        )}
        {diffText && (
          <pre className="max-h-40 overflow-auto border-b border-border bg-surface/60 px-4 py-2 font-mono text-[11px] leading-5 text-text-secondary">
            {diffText}
          </pre>
        )}
        {genError && (
          <div className="border-b border-loss/30 bg-loss/5 px-4 py-2 text-xs text-loss">❌ {genError}</div>
        )}

        {showHistory ? (
          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            {history.length === 0 ? (
              <p className="py-10 text-center text-sm text-text-secondary">暂无生成历史</p>
            ) : (
              <div className="space-y-1.5">
                {history.map((h) => (
                  <div key={h.id} className="flex items-center justify-between rounded-lg border border-border bg-card px-3 py-2 text-sm">
                    <button onClick={() => loadHistoryItem(h.id)} className="min-w-0 flex-1 text-left">
                      <div className="truncate">{h.title}</div>
                      <div className="text-[11px] text-text-muted">
                        {h.time} · {h.form} · {h.mode} · {h.output_len} 字
                      </div>
                    </button>
                    <button
                      onClick={async () => {
                        await studioApi.historyDelete(h.id).catch(() => {})
                        loadSide()
                      }}
                      className="ml-2 shrink-0 text-xs text-text-muted hover:text-loss"
                    >
                      删除
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className="min-h-0 flex-1">
            {output ? (
              <Editor
                height="100%"
                language="python"
                theme="vs-dark"
                value={output}
                onChange={(v: string | undefined) => {
                  outputRef.current = v || ''
                  setOutput(v || '')
                }}
                options={{
                  fontSize: 13, fontFamily: '"JetBrains Mono", monospace',
                  minimap: { enabled: false }, scrollBeyondLastLine: false,
                  padding: { top: 12 }, wordWrap: 'on', tabSize: 4, readOnly: generating,
                }}
              />
            ) : (
              <div className="flex h-full flex-col items-center justify-center text-center">
                <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-accent/40 bg-accent/15">
                  <Sparkles size={26} className="text-accent" />
                </div>
                <h3 className="mt-4 font-medium">描述需求，AI 生成可直接运行的 QMT 策略</h3>
                <p className="mt-1 max-w-md text-sm leading-6 text-text-secondary">
                  生成后自动质检（语法 + Python 3.6 兼容）、可自动修复、diff 对比，
                  一键保存进 QMT 策略目录并注册到「我的策略」。
                </p>
                {generating && <div className="mt-4 streaming-cursor text-accent" />}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
