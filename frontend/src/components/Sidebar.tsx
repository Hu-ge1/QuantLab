import {
  Activity,
  BarChart2,
  Brain,
  Dna,
  Globe2,
  Grid3X3,
  MessageSquare,
  MonitorDot,
  Settings,
  Sparkles,
  TrendingUp,
  Search,
  ListFilter,
} from 'lucide-react'
import { NavLink } from 'react-router-dom'

const NAV = [
  { to: '/chat', icon: MessageSquare, label: 'AI 对话' },
  { to: '/market', icon: Globe2, label: '市场' },
  { to: '/screener', icon: ListFilter, label: '量化选股' },
  { to: '/decision', icon: Brain, label: '贝叶斯决策' },
  { to: '/portfolio', icon: BarChart2, label: '持仓管理' },
  { to: '/strategy', icon: TrendingUp, label: '策略回测' },
  { to: '/evolution', icon: Dna, label: '策略自进化' },
  { to: '/studio', icon: Sparkles, label: '策略工坊' },
  { to: '/grid', icon: Grid3X3, label: '网格交易' },
  { to: '/qmt', icon: MonitorDot, label: 'QMT 实盘' },
  { to: '/analyst', icon: Search, label: '模型分析师' },
  { to: '/settings', icon: Settings, label: '设置' },
]

export default function Sidebar() {
  return (
    <aside className="flex w-16 shrink-0 flex-col border-r border-border bg-surface lg:w-56">
      <div className="flex items-center gap-2.5 border-b border-border px-4 py-4">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-accent/40 bg-accent/20">
          <Activity size={17} className="text-accent" />
        </div>
        <span className="hidden text-sm font-semibold lg:block">QuantLab</span>
      </div>

      <nav className="flex-1 space-y-1 overflow-y-auto p-2">
        {NAV.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              isActive
                ? 'flex items-center gap-3 rounded-lg border border-accent/30 bg-accent/15 px-3 py-2 text-sm text-accent'
                : 'flex items-center gap-3 rounded-lg border border-transparent px-3 py-2 text-sm text-text-secondary hover:bg-card hover:text-text-primary'
            }
          >
            {({ isActive }) => (
              <>
                <Icon size={17} className={isActive ? 'text-accent' : ''} />
                <span className="hidden lg:block">{label}</span>
              </>
            )}
          </NavLink>
        ))}
      </nav>

      <div className="border-t border-border px-4 py-3 text-[11px] text-text-muted">
        <span className="hidden lg:block">量化实验室 v1.0 · 研究用途</span>
        <span className="lg:hidden">v1.0</span>
      </div>
    </aside>
  )
}
