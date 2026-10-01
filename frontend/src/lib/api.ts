// API 层：全部 REST 封装 + TS 类型。SSE 不在此处 —— 聊天与自进化
// 由页面直接用 fetch 流式消费（需要 POST body，EventSource 不适用）。

const BASE = '/api'

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  })
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`${res.status}: ${text}`)
  }
  return res.json()
}

// ── 类型 ────────────────────────────────────────────────────────────────────

export interface MemoryEntry { id: number; key: string; content: string; created_at: string }
export interface ChatSession { id: string; title: string; created_at: string; updated_at: string }
export interface ChatMessage { role: 'user' | 'assistant' | 'system'; content: string; created_at?: string }

export interface Evidence {
  name: string
  direction: 'support' | 'against'
  quality: 'strong' | 'medium' | 'weak'
  note?: string
}
export interface ActionDef { name: string; payoff_if_true: number; payoff_if_false: number }
export interface DecisionRequest {
  hypothesis: string
  prior: number
  timeframe?: string
  success_criteria?: string
  evidences: Evidence[]
  actions?: ActionDef[]
}
export interface BeliefStep {
  step: string
  evidence: string | null
  direction?: string | null
  quality?: string | null
  lr?: number | null
  posterior: number
}
export interface ActionEV {
  name: string
  payoff_if_true: number
  payoff_if_false: number
  expected_value: number
}
export interface DecisionResult {
  hypothesis: string
  timeframe: string
  success_criteria: string
  prior: number
  posterior: number
  belief_trace: BeliefStep[]
  evidence_count: number
  actions: ActionEV[]
  recommendation: {
    action: string
    expected_value: number
    margin_over_runner_up: number
    confidence: string
  }
  sensitivity: {
    prior_sweep: { prior: number; best_action: string }[]
    robust: boolean
    critical_evidence: string[]
    flip_prior_below: number | null
    flip_prior_above: number | null
    note: string
  }
  fragile_evidence: { evidence: string; direction: string; quality: string; lr: number; log_lr: number }[]
  disclaimer: string
}

export interface Position {
  id: number
  code: string
  name: string
  cost: number
  shares: number
  cur_price: number | null
  cost_value: number
  cur_value: number | null
  pnl: number | null
  pnl_pct: number | null
}
export interface PositionCreate { code: string; name?: string; cost: number; shares: number }
export interface PortfolioSummary {
  total_cost: number
  total_value: number
  pnl: number
  pnl_pct: number
  has_live_prices: boolean
  position_count: number
  as_of: string
}

export interface Strategy {
  id: number
  name: string
  description: string
  code: string
  status: 'idle' | 'live'
  created_at: string
  updated_at: string
}
export interface BacktestParams {
  start_date: string
  end_date: string
  initial_capital: number
  benchmark: string
}
export interface BacktestStats {
  total_return: number
  ann_return: number
  benchmark_return: number
  sharpe: number
  max_drawdown: number
  final_value: number
  initial_value: number
  trade_days: number
  trade_count?: number
  total_fees?: number
}
export interface BacktestTrade {
  date: string
  signal_date: string
  code: string
  side: 'buy' | 'sell'
  amount: number
  price: number
  fee: number
}
export interface BacktestResult {
  curve: { date: string; value: number }[]
  norm_curve: { date: string; strategy: number; benchmark?: number }[]
  bench_curve: { date: string; value: number }[]
  source: string
  trades?: BacktestTrade[]
  rejected_orders?: ({ date: string; reason: string } & Record<string, unknown>)[]
  pending_orders?: Record<string, unknown>[]
  run_meta?: {
    engine: string
    code_sha256: string
    params: BacktestParams
    python: string
    elapsed_seconds: number
  }
  stats: BacktestStats
}

export interface ParamSpec { min: number; max: number; step: number; default: number; desc: string }
export interface EvolveMeta {
  param_space: Record<string, ParamSpec>
  strategy: { id: number; name: string; description: string } | null
  default_universe: string[]
  default_config: Record<string, number>
  split: { train: number; val: number; test: number }
}
export interface WindowMetrics {
  ann_return: number
  total_return?: number
  sharpe: number
  max_drawdown: number
  turnover: number
  final: number
  n_days: number
  rebalances?: number
  valid: boolean
}
export interface EvolveVersion {
  version: string
  config: Record<string, number>
  metrics: { train: WindowMetrics; val: WindowMetrics; test?: WindowMetrics }
  kept: boolean
  change: string
  parent: string | null
}
export interface EvolveSummary {
  strategy_id?: number
  strategy_name?: string
  rounds_run: number
  versions_kept: number
  init: { val_sharpe: number; test_sharpe: number; test_ann_return: number; test_max_drawdown: number }
  final: { val_sharpe: number; test_sharpe: number; test_ann_return: number; test_max_drawdown: number }
}

export interface SettingsPayload {
  ai_base_url?: string
  ai_api_key?: string
  ai_model?: string
  ai_provider?: string
  theme?: string
  ffd_api_key?: string
}

export interface ScreenFilters {
  keyword?: string
  market?: 'main'
  profile?: 'balanced' | 'value' | 'momentum'
  exclude_st?: boolean
  above_ma60?: boolean
  strict_uptrend?: boolean
  use_financial_quality?: boolean
  min_market_cap?: number
  max_market_cap?: number
  min_pe?: number
  max_pe?: number
  max_pb?: number
  min_amount_yi?: number
  min_turnover?: number
  min_change?: number
  max_change?: number
  min_gross_margin?: number
  min_revenue_yoy?: number
  min_net_profit_yoy?: number
  limit?: number
  force?: boolean
}
export interface ScreenRow {
  code: string
  name: string
  price: number | null
  change_pct: number | null
  amount_yi: number | null
  turnover: number | null
  pe_ttm: number | null
  pb: number | null
  market_cap_yi: number | null
  float_cap_yi: number | null
  market_cap_basis?: string
  amplitude: number | null
  volume_ratio: number | null
  ma60: number | null
  ma120: number | null
  ma250: number | null
  ma60_distance_pct: number | null
  ma60_120_spread_pct: number | null
  ma120_250_spread_pct: number | null
  trend_stage: string
  strict_uptrend: boolean
  gross_margin: number | null
  revenue_yoy: number | null
  net_profit_yoy: number | null
  financial_report_period?: string | null
  financial_available_at?: string | null
  score: number
  factors: { value: number; quality: number; growth: number; trend: number; strength: number; liquidity: number; stability: number }
}
export interface ScreenResult {
  source: string
  as_of: string
  scanned: number
  matched: number
  profile: string
  profile_label: string
  weights: Record<string, number>
  results: ScreenRow[]
  factor_note: string
  warnings: string[]
  coverage: Record<string, { valid: number; total: number; rate: number }>
  financial_coverage?: { requested: number; returned: number; report_period?: string; fields?: string[]; error?: string }
}
export interface BasketBacktestResult {
  kind: 'ex_post_diagnosis'
  codes: string[]
  requested: string[]
  start_date: string
  end_date: string
  trading_days: number
  coverage_rate: number
  curve: { date: string; basket: number; benchmark?: number | null }[]
  metrics: Record<string, number | null>
  benchmark_metrics: Record<string, number | null> | null
  per_stock: { code: string; name: string; return_pct: number; source: string }[]
  skipped: { code: string; reason: string }[]
  sources: string[]
  benchmark_source: string | null
  warning: string
}

export interface SecurityInfo { code: string; name: string; type: string }
export interface QuoteInfo {
  code: string
  name: string
  source: string
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  change: number
  change_pct: number
}
export interface KlineBar {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

// ── 各命名空间 ──────────────────────────────────────────────────────────────

export const chatApi = {
  sessions: () => req<ChatSession[]>('/chat/sessions'),
  messages: (id: string) => req<ChatMessage[]>(`/chat/sessions/${id}/messages`),
  newSession: () => req<{ session_id: string }>('/chat/sessions', { method: 'POST' }),
  deleteSession: (id: string) => req<{ ok: boolean }>(`/chat/sessions/${id}`, { method: 'DELETE' }),
}

export const memoryApi = {
  list: () => req<MemoryEntry[]>('/chat/memories'),
  remove: (id: number) => req<{ ok: boolean }>(`/chat/memories/${id}`, { method: 'DELETE' }),
  clear: () => req<{ ok: boolean }>('/chat/memories', { method: 'DELETE' }),
}

export const portfolioApi = {
  list: () => req<Position[]>('/portfolio'),
  summary: () => req<PortfolioSummary>('/portfolio/summary'),
  add: (p: PositionCreate) => req<{ ok: boolean; id: number }>('/portfolio', { method: 'POST', body: JSON.stringify(p) }),
  update: (id: number, p: Partial<PositionCreate>) => req<{ ok: boolean }>(`/portfolio/${id}`, { method: 'PUT', body: JSON.stringify(p) }),
  remove: (id: number) => req<{ ok: boolean }>(`/portfolio/${id}`, { method: 'DELETE' }),
  refreshPrices: () => req<{ ok: boolean; updated: number; errors: string[] }>('/portfolio/refresh-prices', { method: 'POST' }),
}

export const strategyApi = {
  list: () => req<Strategy[]>('/strategy'),
  get: (id: number) => req<Strategy>(`/strategy/${id}`),
  create: (s: { name: string; description?: string; code?: string }) =>
    req<{ ok: boolean; id: number }>('/strategy', { method: 'POST', body: JSON.stringify(s) }),
  update: (id: number, s: Partial<{ name: string; description: string; code: string }>) =>
    req<{ ok: boolean }>(`/strategy/${id}`, { method: 'PUT', body: JSON.stringify(s) }),
  remove: (id: number) => req<{ ok: boolean }>(`/strategy/${id}`, { method: 'DELETE' }),
  // 同步请求（非 SSE）：回测通常几秒内完成
  backtest: (id: number, params: BacktestParams) =>
    req<{ ok: boolean; result: BacktestResult }>(`/strategy/${id}/backtest`, { method: 'POST', body: JSON.stringify(params) }),
  backtestDetail: (id: number, resultId: number) =>
    req<{ id: number; created_at: string; params: BacktestParams; result: BacktestResult }>(
      `/strategy/${id}/backtest/${resultId}`,
    ),
  backtestHistory: (id: number) =>
    req<{ id: number; created_at: string; stats: BacktestStats }[]>(`/strategy/${id}/backtest/history`),
  toggleLive: (id: number) => req<{ ok: boolean; status: string }>(`/strategy/${id}/live/toggle`, { method: 'POST' }),
  // 异步回测（后台任务，页面跳转不中断）
  backtestAsync: (id: number, params: BacktestParams) =>
    req<{ ok: boolean; task_id: string; status: string }>(`/strategy/${id}/backtest/async`, { method: 'POST', body: JSON.stringify(params) }),
  backtestTask: (taskId: string) =>
    req<{ task_id: string; status: string; result?: BacktestResult; error?: string; created_at: string }>(
      `/strategy/backtest/task/${taskId}`,
    ),
}

export const decisionApi = {
  meta: () => req<{ quality_lr: Record<string, number>; default_actions: ActionDef[] }>('/decision/meta'),
  analyze: (payload: DecisionRequest) => req<{ ok: boolean; result: DecisionResult }>('/decision/analyze', { method: 'POST', body: JSON.stringify(payload) }),
}

export const evolveApi = {
  meta: (strategyId?: number) => req<EvolveMeta>(`/evolve/meta${strategyId ? `?strategy_id=${strategyId}` : ''}`),
  runs: () => req<{ id: number; created_at: string; summary: EvolveSummary }[]>('/evolve/runs'),
  getRun: (id: number) =>
    req<{
      id: number
      universe: string[]
      best_config: Record<string, number>
      history: EvolveVersion[]
      summary: EvolveSummary
      created_at: string
    }>(`/evolve/runs/${id}`),
  // /evolve/run 是 SSE —— 由页面直接 fetch 流式消费
}

export const settingsApi = {
  get: () => req<Record<string, string>>('/settings'),
  update: (s: SettingsPayload) => req<{ ok: boolean }>('/settings', { method: 'PUT', body: JSON.stringify(s) }),
  testDatasource: () => req<{ ok: boolean; message: string }>('/settings/test-datasource', { method: 'POST' }),
  datasourceStatus: () => req<{ mode: string; primary: string; fallback: string; last_error: string; note: string }>('/settings/datasource'),
  llmStatus: () =>
    req<{ has_key: boolean; from_env: boolean; base_url: string; model: string; provider: string }>('/settings/llm-status'),
}

export interface AnalystMetric {
  date: string
  value: number
  avg_5d?: number
  change?: number
  avg_change_5d?: number
}
export interface AnalystResult {
  code: string
  name?: string
  source: string
  desire: AnalystMetric | null
  focus: AnalystMetric | null
  score: AnalystMetric | null
  institution: AnalystMetric | null
  note?: string
}

export const quoteApi = {
  search: (q: string, types = 'stock,etf') =>
    req<SecurityInfo[]>(`/quote/search?q=${encodeURIComponent(q)}&types=${types}`),
  quote: (code: string) => req<QuoteInfo>(`/quote/quote/${encodeURIComponent(code)}`),
  kline: (code: string, frequency = 'daily', count = 60) =>
    req<{ code: string; source: string; bars: KlineBar[] }>(`/quote/kline/${encodeURIComponent(code)}?frequency=${frequency}&count=${count}`),
  analyst: (code: string) => req<AnalystResult>(`/quote/analyst/${encodeURIComponent(code)}`),
}

// ── QMT 只读接入 ────────────────────────────────────────────────────────────

export interface QmtStatus {
  studio: {
    available: boolean
    online: boolean
    error: string
    last_seen: string
    trading_phase: string
    url: string
  }
  xtquant: {
    installed: boolean
    site: string
    error: string
  }
  readonly: boolean
}

export interface QmtAccount {
  source: string
  online: boolean
  error?: string
  hint?: string
  last_seen?: string
  trading_phase?: Record<string, unknown>
  market?: {
    limit_up?: number
    limit_down?: number
    up_count?: number
    down_count?: number
    index_pct?: number | null
    time?: string
  } | null
  account?: {
    accountID?: string
    balance?: number
    available?: number
    market_value?: number
    profit?: number
    [k: string]: unknown
  }
  positions?: QmtPosition[]
  orders?: QmtOrder[]
}

export interface QmtPosition {
  code: string
  name?: string
  volume?: number
  can_use?: number
  cost?: number
  market_value?: number
  profit?: number
  [k: string]: unknown
}

export interface QmtOrder {
  code: string
  name?: string
  side?: string
  price?: number
  volume?: number
  status?: string
  [k: string]: unknown
}

export const qmtApi = {
  status: () => req<QmtStatus>('/qmt/status'),
  probe: () => req<QmtStatus>('/qmt/probe', { method: 'POST' }),
  account: () => req<QmtAccount>('/qmt/account'),
  realtime: (codes: string[]) =>
    req<{ available: boolean; source: string; ticks: Record<string, { price: number; time: string }> }>(
      '/qmt/realtime',
      { method: 'POST', body: JSON.stringify({ codes }) },
    ),
  importPositions: (onlyNew = true) =>
    req<{ ok: boolean; imported?: number; skipped?: number; error?: string; hint?: string; source?: string }>(
      '/qmt/import-positions',
      { method: 'POST', body: JSON.stringify({ only_new: onlyNew }) },
    ),
}

// ── 市场复盘 ────────────────────────────────────────────────────────────────

export interface MarketIndex {
  code: string
  name: string
  date: string
  close: number
  change_pct: number | null
  chg_5d: number | null
  chg_20d: number | null
  spark?: number[]
}
export interface MarketSector {
  name: string
  change_pct: number
  count: number
  leader: string
  leader_code: string
  leader_chg: number
  amount_yi: number
}
export interface MarketActivity {
  up: number
  down: number
  limit_up: number
  real_limit_up?: number
  limit_down: number
  flat?: number
  suspended?: number
  active_pct: number
  date: string
  breadth: number | null
}
export interface MarketOverview {
  activity: MarketActivity | null
  indices: MarketIndex[]
  sectors: MarketSector[] | null
  conclusions: string[]
  generated_at: string
  source: string
}

export const marketApi = {
  overview: (force = false) => req<MarketOverview>(`/market/overview${force ? '?force=true' : ''}`),
}

export const screenerApi = {
  run: (filters: ScreenFilters) =>
    req<ScreenResult>('/screener/run', { method: 'POST', body: JSON.stringify(filters) }),
  backtest: (codes: string[], lookback = 252) =>
    req<BasketBacktestResult>('/screener/backtest', { method: 'POST', body: JSON.stringify({ codes, lookback }) }),
}

// ── 交易执行层（L1 手动 / L2 信号执行 / L3 全自动）──────────────────────────

export interface TradingStatus {
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
  queued_commands: number
  inflight_commands: number
}
export interface TradingRule { strategy: string; code: string; side: string; volume: number }
export interface AuditEntry { time: string; event: string; [k: string]: unknown }

export const tradingApi = {
  status: () => req<TradingStatus>('/trading/status'),
  updateConfig: (body: Partial<{
    allow_order: boolean; auto_trading: boolean; circuit_loss_pct: number;
    order_max_volume: number; order_max_per_day: number; order_allowlist: string;
  }>) => req<{ ok: boolean; error?: string }>('/trading/config', { method: 'PUT', body: JSON.stringify(body) }),
  kill: () => req<{ ok: boolean }>('/trading/kill', { method: 'POST' }),
  enable: () => req<{ ok: boolean }>('/trading/enable', { method: 'POST' }),
  order: (body: { code: string; side: string; prType?: string; price?: number; volume: number }) =>
    req<{ ok?: boolean; id?: string; error?: string }>('/trading/order', { method: 'POST', body: JSON.stringify(body) }),
  signalExec: (body: { code: string; action: string; price?: number; volume: number; strategy?: string }) =>
    req<{ ok?: boolean; id?: string; error?: string }>('/trading/signal-exec', { method: 'POST', body: JSON.stringify(body) }),
  setRules: (rules: TradingRule[]) =>
    req<{ ok: boolean; rules: TradingRule[] }>('/trading/rules', { method: 'PUT', body: JSON.stringify({ rules }) }),
  audit: () => req<{ audit: AuditEntry[] }>('/trading/audit'),
  testSignal: (body: Partial<{ code: string; action: string; price: number; strategy: string }>) =>
    req<{ ok: boolean; signal: { code: string; action: string; price?: number }; auto_will_execute: boolean; error?: string }>(
      '/trading/test-signal', { method: 'POST', body: JSON.stringify(body) },
    ),
}

// ── QMT 桥接（已合并 studio）───────────────────────────────────────────────

export interface BridgeState {
  online: boolean
  last_seen: string
  state: {
    mode?: string
    account?: Record<string, unknown>
    positions?: QmtPosition[]
    ticks?: Record<string, { lastPrice?: number; lastClose?: number }>
    market?: Record<string, unknown> | null
    orders?: Record<string, unknown>[]
    deals?: Record<string, unknown>[]
  }
  results: Record<string, unknown>[]
  signals: { time: string; strategy: string; code: string; action: string; price?: number; note?: string }[]
  market_history: { time: string; limit_up?: number; limit_down?: number; up?: number; down?: number; index_pct?: number | null }[]
  asset_history?: { t: string; total: number; avail: number; mv: number }[]
  session: { trading: boolean; label: string }
}

export interface TickPoint { t: string; p: number }
export interface DailyReport { date: string; created?: string; [k: string]: unknown }

export const bridgeApi = {
  state: () => req<BridgeState>('/bridge/state'),
  ticks: (code: string) => req<{ code: string; points: TickPoint[] }>(`/bridge/ticks/${encodeURIComponent(code)}`),
  command: (body: Record<string, unknown>) =>
    req<{ ok?: boolean; id?: string; error?: string }>('/bridge/command', { method: 'POST', body: JSON.stringify(body) }),
  watchlist: (codes: string[]) =>
    req<{ ok: boolean; codes: string[] }>('/bridge/watchlist', { method: 'POST', body: JSON.stringify({ codes }) }),
  install: () => req<{ ok: boolean; path?: string; error?: string; note?: string; registered?: boolean; register_msg?: string; backup?: string }>('/bridge/install', { method: 'POST' }),
  reports: () => req<{ reports: DailyReport[] }>('/bridge/reports'),
  watchlistGet: () => req<{ codes: string[] }>('/bridge/watchlist'),
}

// ── 策略工坊（已合并 studio）───────────────────────────────────────────────

export interface StudioTemplate {
  id?: string
  name?: string
  desc?: string
  requirement?: string
  [k: string]: unknown
}
export interface StudioHistoryItem {
  id: string
  title: string
  form: string
  mode: string
  time: string
  output_len: number
}
export interface StudioIssue { [k: string]: unknown }

export const studioApi = {
  templates: () => req<{ categories: string[]; templates: StudioTemplate[] }>('/studio/templates'),
  knowledge: () => req<{ docs: { name: string; size: number }[] }>('/studio/knowledge'),
  check: (code: string, form = 'qmt_builtin') =>
    req<{ issues: string[] }>('/studio/check', { method: 'POST', body: JSON.stringify({ code, form }) }),
  repair: (code: string, form = 'qmt_builtin') =>
    req<{ ok: boolean; code: string; remaining_issues: string[] }>('/studio/repair', { method: 'POST', body: JSON.stringify({ code, form }) }),
  diff: (a: string, b: string) => req<{ diff: string }>('/studio/diff', { method: 'POST', body: JSON.stringify({ a, b }) }),
  history: (q = '') => req<{ items: StudioHistoryItem[] }>(`/studio/history?q=${encodeURIComponent(q)}`),
  historyGet: (id: string) => req<{ id: string; title: string; output: string; requirement: string; form: string; mode: string; time: string }>(`/studio/history/${id}`),
  historyDelete: (id: string) => req<{ ok: boolean }>(`/studio/history/${id}`, { method: 'DELETE' }),
  historyAdd: (body: Record<string, unknown>) => req<{ id: string }>('/studio/history', { method: 'POST', body: JSON.stringify(body) }),
  save: (body: { filename: string; code: string; overwrite?: boolean }) =>
    req<{ ok: boolean; path?: string; registered?: boolean; register_msg?: string; client_running?: boolean; backup?: string; error?: string; exists?: boolean }>(
      '/studio/save', { method: 'POST', body: JSON.stringify(body) },
    ),
  qmtstatus: () => req<{ python_dir: string; python_dir_ok: boolean; xml_found: boolean; client_running: boolean; registry: { name: string; time: string }[] }>('/studio/qmtstatus'),
  configGet: () => req<{ qmt_python_dir: string; temperature: number; allow_order: boolean; order_max_volume: number; order_max_per_day: number; order_allowlist: string[]; emotion_stats: boolean; emotion_interval_min: number; max_history: number }>('/studio/config'),
  configSet: (body: Partial<{ qmt_python_dir: string; temperature: number; allow_order: boolean; order_max_volume: number; order_max_per_day: number; order_allowlist: string; emotion_stats: boolean; emotion_interval_min: number; max_history: number }>) =>
    req<{ ok: boolean }>('/studio/config', { method: 'PUT', body: JSON.stringify(body) }),
}
