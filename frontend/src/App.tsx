import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import Sidebar from './components/Sidebar'
import { ToastProvider } from './components/Toast'

const ChatPage = lazy(() => import('./pages/ChatPage'))
const DecisionPage = lazy(() => import('./pages/DecisionPage'))
const MarketPage = lazy(() => import('./pages/MarketPage'))
const ScreenerPage = lazy(() => import('./pages/ScreenerPage'))
const PortfolioPage = lazy(() => import('./pages/PortfolioPage'))
const StrategyPage = lazy(() => import('./pages/StrategyPage'))
const EvolutionPage = lazy(() => import('./pages/EvolutionPage'))
const StudioPage = lazy(() => import('./pages/StudioPage'))
const GridPage = lazy(() => import('./pages/GridPage'))
const QmtPage = lazy(() => import('./pages/QmtPage'))
const SettingsPage = lazy(() => import('./pages/SettingsPage'))
const AnalystPage = lazy(() => import('./pages/AnalystPage'))

const TITLES: Record<string, { title: string; sub: string }> = {
  '/chat': { title: 'AI 对话', sub: '与量化研究 AI 助手对话，每个数字都有来源' },
  '/market': { title: '市场', sub: '指数看板 · 市场宽度 · 自动复盘要点' },
  '/screener': { title: '量化选股', sub: '全市场批量筛选 · 多因子排序 · FFD 数据优先' },
  '/decision': { title: '贝叶斯决策', sub: '把拍脑袋变成可追溯的概率推理' },
  '/portfolio': { title: '持仓管理', sub: '成本、市值与盈亏一目了然' },
  '/strategy': { title: '策略回测', sub: '浏览器里写策略，服务端沙箱执行' },
  '/evolution': { title: '策略自进化', sub: 'AutoResearch 约束化自动迭代' },
  '/studio': { title: '策略工坊', sub: '描述需求，AI 生成可直接运行的 QMT 策略' },
  '/grid': { title: '网格交易', sub: '价格区间自动分档，低买高卖捕捉震荡价差' },
  '/qmt': { title: 'QMT 实盘', sub: '桥接实时看板：资产/持仓/情绪/信号（只读）' },
  '/analyst': { title: '模型分析师', sub: '东方财富股吧评分、机构参与度、用户关注度（akshare）' },
  '/settings': { title: '设置', sub: '模型密钥、数据源与 QMT 配置' },
}

function PageTitle({ title, sub }: { title: string; sub: string }) {
  return (
    <div className="border-b border-border bg-surface/60 px-6 py-4">
      <h1 className="text-lg font-semibold text-text-primary">{title}</h1>
      <p className="mt-0.5 text-xs text-text-secondary">{sub}</p>
    </div>
  )
}

function PageFallback() {
  return (
    <div className="flex h-full items-center justify-center text-sm text-text-secondary">
      页面加载中…
    </div>
  )
}

export default function App() {
  return (
    <ToastProvider>
      <div className="flex h-screen overflow-hidden bg-base text-text-primary">
        <Sidebar />
        <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
          <Routes>
            <Route path="/chat" element={<PageTitle {...TITLES['/chat']} />} />
            <Route path="/market" element={<PageTitle {...TITLES['/market']} />} />
            <Route path="/screener" element={<PageTitle {...TITLES['/screener']} />} />
            <Route path="/decision" element={<PageTitle {...TITLES['/decision']} />} />
            <Route path="/portfolio" element={<PageTitle {...TITLES['/portfolio']} />} />
            <Route path="/strategy" element={<PageTitle {...TITLES['/strategy']} />} />
            <Route path="/evolution" element={<PageTitle {...TITLES['/evolution']} />} />
            <Route path="/studio" element={<PageTitle {...TITLES['/studio']} />} />
            <Route path="/grid" element={<PageTitle {...TITLES['/grid']} />} />
            <Route path="/qmt" element={<PageTitle {...TITLES['/qmt']} />} />
            <Route path="/analyst" element={<PageTitle {...TITLES['/analyst']} />} />
            <Route path="/settings" element={<PageTitle {...TITLES['/settings']} />} />
          </Routes>
          <div className="min-h-0 flex-1 overflow-hidden">
            <Suspense fallback={<PageFallback />}>
              <Routes>
                <Route path="/" element={<Navigate to="/chat" replace />} />
                <Route path="/chat" element={<ChatPage />} />
                <Route path="/market" element={<MarketPage />} />
                <Route path="/screener" element={<ScreenerPage />} />
                <Route path="/decision" element={<DecisionPage />} />
                <Route path="/portfolio" element={<PortfolioPage />} />
                <Route path="/strategy" element={<StrategyPage />} />
                <Route path="/evolution" element={<EvolutionPage />} />
                <Route path="/studio" element={<StudioPage />} />
                <Route path="/grid" element={<GridPage />} />
                <Route path="/qmt" element={<QmtPage />} />
                <Route path="/analyst" element={<AnalystPage />} />
                <Route path="/settings" element={<SettingsPage />} />
              </Routes>
            </Suspense>
          </div>
        </main>
      </div>
    </ToastProvider>
  )
}
