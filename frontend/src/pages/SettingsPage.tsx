import {
  Bot,
  CheckCircle,
  Database,
  Eye,
  EyeOff,
  Info,
  KeyRound,
  Loader2,
  Plug,
  Search,
  Trash2,
  XCircle,
} from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import {
  memoryApi,
  qmtApi,
  quoteApi,
  settingsApi,
  type MemoryEntry,
  type QmtStatus,
  type SecurityInfo,
} from '../lib/api'
import { useToast } from '../components/Toast'

const AI_PROVIDERS = [
  { id: 'deepseek', name: 'DeepSeek', url: 'https://api.deepseek.com/v1', models: ['deepseek-chat', 'deepseek-reasoner'] },
  { id: 'openai', name: 'OpenAI', url: 'https://api.openai.com/v1', models: ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo'] },
  { id: 'qwen', name: '通义千问', url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', models: ['qwen-max', 'qwen-plus', 'qwen-turbo'] },
  { id: 'moonshot', name: 'Kimi', url: 'https://api.moonshot.cn/v1', models: ['moonshot-v1-32k', 'moonshot-v1-128k'] },
  { id: 'anthropic', name: 'Anthropic', url: '', models: ['claude-sonnet-4-5', 'claude-haiku-3-5'] },
  { id: 'custom', name: '自定义', url: '', models: [] },
]

const MASK = '••••••••'

export default function SettingsPage() {
  const toast = useToast()
  const [provider, setProvider] = useState('deepseek')
  const [baseUrl, setBaseUrl] = useState('https://api.deepseek.com/v1')
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState('deepseek-chat')
  const [showKey, setShowKey] = useState(false)
  const [ffdKey, setFfdKey] = useState('')
  const [saved, setSaved] = useState(false)

  const [dsStatus, setDsStatus] = useState<{
    mode: string
    primary: string
    fallback: string
    last_error: string
    note: string
  } | null>(null)
  const [testing, setTesting] = useState(false)
  const [testOk, setTestOk] = useState<boolean | null>(null)
  const [testMsg, setTestMsg] = useState('')

  const [searchQ, setSearchQ] = useState('')
  const [hits, setHits] = useState<SecurityInfo[]>([])
  const [llm, setLlm] = useState<{
    has_key: boolean
    from_env: boolean
    base_url: string
    model: string
    provider: string
  } | null>(null)
  const [memories, setMemories] = useState<MemoryEntry[]>([])
  const [qmt, setQmt] = useState<QmtStatus | null>(null)
  const [qmtTesting, setQmtTesting] = useState(false)

  const loadQmt = useCallback(() => {
    qmtApi.status().then(setQmt).catch(() => {})
  }, [])

  const loadMemories = useCallback(() => {
    memoryApi
      .list()
      .then(setMemories)
      .catch(() => {})
  }, [])

  useEffect(() => {
    settingsApi
      .get()
      .then((s) => {
        if (s.ai_provider) setProvider(s.ai_provider)
        if (s.ai_base_url) setBaseUrl(s.ai_base_url)
        if (s.ai_model) setModel(s.ai_model)
        if (s.ai_api_key) setApiKey(s.ai_api_key)
        if (s.ffd_api_key) setFfdKey(s.ffd_api_key)
      })
      .catch(() => {})
    settingsApi
      .datasourceStatus()
      .then(setDsStatus)
      .catch(() => {})
    settingsApi.llmStatus().then(setLlm).catch(() => {})
    loadMemories()
    loadQmt()
  }, [loadMemories, loadQmt])

  // 搜索体验（400ms 防抖）
  useEffect(() => {
    if (searchQ.trim().length < 2) {
      setHits([])
      return
    }
    const t = setTimeout(async () => {
      try {
        setHits(await quoteApi.search(searchQ.trim()))
      } catch {
        setHits([])
      }
    }, 400)
    return () => clearTimeout(t)
  }, [searchQ])

  const pickProvider = (id: string) => {
    setProvider(id)
    const p = AI_PROVIDERS.find((x) => x.id === id)
    if (p?.url) setBaseUrl(p.url)
    if (p?.models.length) setModel(p.models[0])
    setSaved(false)
  }

  const saveAI = useCallback(async () => {
    try {
      await settingsApi.update({
        ai_provider: provider,
        ai_base_url: baseUrl,
        ai_api_key: apiKey && apiKey !== MASK ? apiKey : undefined,
        ai_model: model,
      })
      setSaved(true)
      toast.success('AI 模型设置已保存')
    } catch (e) {
      toast.error(`保存失败：${e}`)
    }
  }, [provider, baseUrl, apiKey, model, toast])

  const testDatasource = async () => {
    setTesting(true)
    setTestOk(null)
    try {
      const r = await settingsApi.testDatasource()
      setTestOk(r.ok)
      setTestMsg(r.message)
      settingsApi.datasourceStatus().then(setDsStatus).catch(() => {})
    } catch (e) {
      setTestOk(false)
      setTestMsg(`测试失败：${e}`)
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-5 p-6">
        {/* AI 模型设置 */}
        <Section icon={<Bot size={16} className="text-accent" />} title="AI 模型设置">
          <div className="grid grid-cols-3 gap-2 md:grid-cols-6">
            {AI_PROVIDERS.map((p) => (
              <button
                key={p.id}
                onClick={() => pickProvider(p.id)}
                className={`rounded-lg border px-2 py-2 text-xs ${
                  provider === p.id
                    ? 'border-accent/50 bg-accent/15 text-accent'
                    : 'border-border bg-surface text-text-secondary hover:text-text-primary'
                }`}
              >
                {p.name}
              </button>
            ))}
          </div>
          <div className="mt-4 space-y-3">
            {provider !== 'anthropic' && (
              <div>
                <label className="text-xs text-text-secondary">Base URL（OpenAI 兼容）</label>
                <input
                  value={baseUrl}
                  onChange={(e) => {
                    setBaseUrl(e.target.value)
                    setSaved(false)
                  }}
                  className="num mt-1 w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-accent/50"
                />
              </div>
            )}
            <div>
              <label className="text-xs text-text-secondary">API Key</label>
              <div className="relative mt-1">
                <input
                  type={showKey ? 'text' : 'password'}
                  value={apiKey}
                  onChange={(e) => {
                    setApiKey(e.target.value)
                    setSaved(false)
                  }}
                  placeholder="sk-..."
                  className="w-full rounded-lg border border-border bg-surface px-3 py-2 pr-10 text-sm outline-none focus:border-accent/50"
                />
                <button
                  onClick={() => setShowKey(!showKey)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary"
                >
                  {showKey ? <EyeOff size={15} /> : <Eye size={15} />}
                </button>
              </div>
            </div>
            <div>
              <label className="text-xs text-text-secondary">模型名</label>
              <div className="mt-1 flex gap-2">
                <input
                  value={model}
                  onChange={(e) => {
                    setModel(e.target.value)
                    setSaved(false)
                  }}
                  className="num flex-1 rounded-lg border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-accent/50"
                />
                {AI_PROVIDERS.find((p) => p.id === provider)?.models.length ? (
                  <select
                    value=""
                    onChange={(e) => e.target.value && setModel(e.target.value)}
                    className="rounded-lg border border-border bg-surface px-2 text-xs text-text-secondary outline-none"
                  >
                    <option value="">预设</option>
                    {AI_PROVIDERS.find((p) => p.id === provider)?.models.map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </select>
                ) : null}
              </div>
            </div>
            <div className="flex items-center gap-3">
              <button
                onClick={saveAI}
                className="rounded-lg bg-accent px-4 py-2 text-sm text-white hover:bg-accent/85"
              >
                保存设置
              </button>
              {saved && (
                <span className="flex items-center gap-1 text-xs text-gain">
                  <CheckCircle size={13} /> 已保存到本地数据库
                </span>
              )}
            </div>
            {llm?.has_key && (
              <div
                className={`rounded-lg border p-3 text-xs leading-5 ${
                  llm.from_env
                    ? 'border-warn/30 bg-warn/5 text-text-secondary'
                    : 'border-gain/30 bg-gain/5 text-text-secondary'
                }`}
              >
                {llm.from_env ? (
                  <>
                    <KeyRound size={12} className="mr-1 inline text-warn" />
                    当前使用<b>环境变量中的 API Key</b>（未存入数据库）。生效配置：
                    <span className="num text-text-primary">{llm.base_url}</span> · 模型{' '}
                    <span className="num text-text-primary">{llm.model}</span>。
                    在上方填写并保存 Key 可覆盖。
                  </>
                ) : (
                  <>
                    <CheckCircle size={12} className="mr-1 inline text-gain" />
                    AI 已就绪：<span className="num text-text-primary">{llm.base_url}</span> · 模型{' '}
                    <span className="num text-text-primary">{llm.model}</span>
                  </>
                )}
              </div>
            )}
          </div>
        </Section>

        <Section icon={<Database size={16} className="text-teal" />} title="FFD 本地数据能力">
          <p className="mb-3 text-xs leading-5 text-text-secondary">量化选股优先读取 FFD 全市场日频资产，缺失时才降级到腾讯批量行情。密钥仅保存在本地 SQLite；已通过环境变量配置时可留空。</p>
          <div className="flex gap-2">
            <input type="password" value={ffdKey} onChange={(e) => setFfdKey(e.target.value)} placeholder="FFD API Key（可选）" className="flex-1 rounded-lg border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-accent/50" />
            <button onClick={async () => { try { await settingsApi.update({ ffd_api_key: ffdKey && ffdKey !== MASK ? ffdKey : undefined }); toast.success('FFD 设置已保存') } catch (e) { toast.error(`保存失败：${e}`) } }} className="rounded-lg bg-teal px-4 py-2 text-sm text-white">保存</button>
          </div>
        </Section>

        {/* 数据源设置 */}
        <Section icon={<Database size={16} className="text-teal" />} title="行情数据源">
          <div className="rounded-lg border border-teal/30 bg-teal/5 p-3 text-xs leading-5 text-text-secondary">
            QuantLab 使用 <span className="text-teal">akshare</span> 免费公开接口（东方财富/新浪/中证指数），
            无需注册任何账号。接口失效或断网时自动切换 <span className="text-warn">demo 模拟数据</span>，
            全部功能仍可运行（结果带「demo·模拟数据」标注）。当前状态：
            <span className="ml-1 font-medium text-text-primary">
              {dsStatus?.mode === 'akshare'
                ? '真实数据（akshare）'
                : dsStatus?.mode === 'demo'
                  ? 'demo 模拟数据'
                  : '待探测'}
            </span>
            {dsStatus?.last_error && (
              <div className="mt-1 text-[11px] text-loss">最近错误：{dsStatus.last_error}</div>
            )}
          </div>
          <div className="mt-3 flex items-center gap-3">
            <button
              onClick={testDatasource}
              disabled={testing}
              className="flex items-center gap-1.5 rounded-lg border border-teal/40 bg-teal/15 px-4 py-2 text-sm text-teal hover:bg-teal/25 disabled:opacity-50"
            >
              {testing ? <Loader2 size={14} className="animate-spin" /> : null}
              测试数据源
            </button>
            {testOk === true && (
              <span className="flex items-center gap-1 text-xs text-gain">
                <CheckCircle size={13} /> {testMsg}
              </span>
            )}
            {testOk === false && (
              <span className="flex items-center gap-1 text-xs text-loss">
                <XCircle size={13} /> {testMsg}
              </span>
            )}
          </div>
        </Section>

        {/* 证券查询 */}
        <Section icon={<Search size={16} className="text-accent" />} title="证券 / 基金查询">
          <input
            value={searchQ}
            onChange={(e) => setSearchQ(e.target.value)}
            placeholder="输入代码或名称关键词，如 600519 / 茅台 / 沪深300"
            className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-accent/50"
          />
          {hits.length > 0 && (
            <div className="mt-3 overflow-hidden rounded-lg border border-border">
              <table className="w-full text-sm">
                <tbody>
                  {hits.map((h) => (
                    <tr key={`${h.type}-${h.code}`} className="border-b border-border/50 last:border-0">
                      <td className="num px-3 py-2 text-teal">{h.code}</td>
                      <td className="px-3 py-2">{h.name}</td>
                      <td className="px-3 py-2 text-right text-xs text-text-muted">{h.type}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>

        {/* QMT 只读接入 */}
        <Section icon={<Plug size={16} className="text-teal" />} title="QMT 接入（只读）">
          <div className="rounded-lg border border-teal/30 bg-teal/5 p-3 text-xs leading-5 text-text-secondary">
            QuantLab 以<b>只读</b>方式接入本机 QMT（无任何下单接口），两条通道自动合并：
            <div className="mt-1.5 space-y-1">
              <div>
                <span className="text-teal">① studio 桥接</span>
                （qmt-strategy-studio 的桥接策略每 2 秒推送账户/持仓/委托）：
                {qmt ? (
                  qmt.studio.available ? (
                    qmt.studio.online ? (
                      <span className="text-gain">在线 ✓（{qmt.studio.last_seen.slice(11, 19) || '刚刚'}）</span>
                    ) : (
                      <span className="text-warn">已找到服务，桥接未在线（在 QMT 中运行桥接策略后变绿）</span>
                    )
                  ) : (
                    <span className="text-loss">未找到 studio 目录</span>
                  )
                ) : (
                  <span className="text-text-muted">检测中…</span>
                )}
              </div>
              <div>
                <span className="text-teal">② MiniQMT 直连</span>
                （xtdata 行情 + xttrader 只读查询，QMT 以极简模式运行时可用）：
                {qmt ? (
                  qmt.xtquant.installed ? (
                    <span className="text-gain">xtquant 就绪 ✓</span>
                  ) : (
                    <span className="text-warn">{qmt.xtquant.error}</span>
                  )
                ) : (
                  <span className="text-text-muted">检测中…</span>
                )}
              </div>
            </div>
            {qmt?.studio.trading_phase && (
              <div className="mt-1.5 text-text-muted">当前时段：{qmt.studio.trading_phase}</div>
            )}
          </div>
          <div className="mt-3 flex items-center gap-3">
            <button
              onClick={async () => {
                setQmtTesting(true)
                try {
                  setQmt(await qmtApi.probe())
                } finally {
                  setQmtTesting(false)
                }
              }}
              disabled={qmtTesting}
              className="flex items-center gap-1.5 rounded-lg border border-teal/40 bg-teal/15 px-4 py-2 text-sm text-teal hover:bg-teal/25 disabled:opacity-50"
            >
              {qmtTesting ? <Loader2 size={14} className="animate-spin" /> : null}
              测试 QMT 连接
            </button>
            {qmt?.studio.online && (
              <span className="flex items-center gap-1 text-xs text-gain">
                <CheckCircle size={13} /> 桥接在线，可到「持仓管理」查看实时账户
              </span>
            )}
          </div>
        </Section>

        {/* 长期记忆管理 */}
        <Section icon={<Database size={16} className="text-teal" />} title="AI 长期记忆">
          {memories.length === 0 ? (
            <p className="text-xs leading-5 text-text-secondary">
              还没有记忆。在 AI 对话里说「记住我偏好低风险」之类的话，AI 会调用 memory_save 存入记忆，下次对话自动加载。
            </p>
          ) : (
            <>
              <div className="max-h-56 space-y-1.5 overflow-y-auto">
                {memories.map((m) => (
                  <div
                    key={m.id}
                    className="flex items-start justify-between gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-xs"
                  >
                    <div className="min-w-0">
                      <span className="rounded bg-teal/15 px-1.5 py-0.5 text-teal">{m.key}</span>
                      <span className="ml-2 text-text-primary">{m.content}</span>
                    </div>
                    <button
                      onClick={async () => {
                        await memoryApi.remove(m.id).catch((e) => toast.error(`删除失败：${e}`))
                        loadMemories()
                      }}
                      className="shrink-0 text-text-muted hover:text-loss"
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                ))}
              </div>
              <button
                onClick={async () => {
                  if (!window.confirm('确认清空全部记忆？')) return
                  await memoryApi.clear().catch((e) => toast.error(`清空失败：${e}`))
                  toast.success('记忆已清空')
                  loadMemories()
                }}
                className="mt-3 rounded-lg border border-loss/40 bg-loss/10 px-3 py-1.5 text-xs text-loss hover:bg-loss/20"
              >
                清空全部记忆
              </button>
            </>
          )}
        </Section>

        {/* 使用说明 */}
        <Section icon={<Info size={16} className="text-warn" />} title="使用说明">
          <ul className="list-disc space-y-1.5 pl-4 text-xs leading-5 text-text-secondary">
            <li>所有密钥只存在你本地的 SQLite（backend/data/quant_lab.db），不上传任何服务器</li>
            <li>不填 AI Key 时，策略回测 / 策略自进化 / 贝叶斯决策 / 持仓管理均可正常使用</li>
            <li>AI 对话支持任意 OpenAI 兼容接口（DeepSeek / 通义 / Kimi / OpenAI）及 Anthropic</li>
            <li>行情为收盘级数据，仅供研究演示，不构成投资建议</li>
          </ul>
        </Section>

        <div className="pb-6" />
      </div>
    </div>
  )
}

function Section({
  icon,
  title,
  children,
}: {
  icon: React.ReactNode
  title: string
  children: React.ReactNode
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-5">
      <div className="mb-4 flex items-center gap-2">
        {icon}
        <h2 className="text-sm font-semibold">{title}</h2>
      </div>
      {children}
    </div>
  )
}
