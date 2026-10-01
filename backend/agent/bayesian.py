"""贝叶斯决策引擎 — 把「要不要买/加仓/止盈」变成可追溯、可复现的概率推理。

纯数学、零 LLM：先验 + 分级证据 → 对数几率逐条更新 → 后验 →
各行动的期望值（EV）对比 → 敏感性分析（结论稳不稳、最依赖哪条证据）。
"""
from __future__ import annotations

import math

# 证据质量 → 似然比基数 LR = P(证据|成立) / P(证据|不成立)
# 参考贝叶斯诊断学的量级区间；"反对"方向的证据取倒数
QUALITY_LR = {"strong": 5.0, "medium": 2.5, "weak": 1.5}

DEFAULT_ACTIONS = [
    {"name": "重仓买入", "payoff_if_true": 18.0, "payoff_if_false": -15.0},
    {"name": "建仓试探", "payoff_if_true": 8.0, "payoff_if_false": -6.0},
    {"name": "观望", "payoff_if_true": -1.0, "payoff_if_false": 1.0},
    {"name": "减仓/清仓", "payoff_if_true": -8.0, "payoff_if_false": 6.0},
]

DISCLAIMER = "本分析为决策支持工具，基于你提供的先验与证据，不构成投资建议。"


def _clamp_prob(p: float) -> float:
    return min(max(float(p), 1e-6), 1 - 1e-6)


def _logodds(p: float) -> float:
    p = _clamp_prob(p)
    return math.log(p / (1 - p))


def _is_against(direction: str) -> bool:
    return str(direction).lower() in ("against", "反对", "negative", "-")


def _evidence_lr(ev: dict) -> float:
    base = QUALITY_LR.get(str(ev.get("quality", "medium")).lower(), 2.5)
    lr = base if not _is_against(ev.get("direction", "support")) else 1.0 / base
    return max(lr, 1e-6)


def run_bayesian_decision(
    hypothesis: str,
    prior: float,
    evidences: list[dict],
    actions: list[dict] | None = None,
    timeframe: str = "",
    success_criteria: str = "",
) -> dict:
    prior = _clamp_prob(prior)
    actions = actions or DEFAULT_ACTIONS

    # ── 对数几率逐条更新，记录信念轨迹 ──
    lo = _logodds(prior)
    belief_trace: list[dict] = [{
        "step": "先验", "evidence": None, "direction": None,
        "quality": None, "lr": None, "posterior": round(prior, 4),
    }]
    for ev in evidences:
        lr = _evidence_lr(ev)
        lo += math.log(lr)
        posterior = 1 / (1 + math.exp(-lo))
        belief_trace.append({
            "step": "更新", "evidence": ev.get("name", ""),
            "direction": ev.get("direction", "support"),
            "quality": ev.get("quality", "medium"),
            "lr": round(lr, 3), "posterior": round(posterior, 4),
        })
    posterior = _clamp_prob(1 / (1 + math.exp(-lo)))

    # ── 各行动期望值 EV = P·payoff_true + (1-P)·payoff_false ──
    ev_rows = []
    for a in actions:
        expected = posterior * float(a["payoff_if_true"]) + (1 - posterior) * float(a["payoff_if_false"])
        ev_rows.append({
            "name": a["name"],
            "payoff_if_true": float(a["payoff_if_true"]),
            "payoff_if_false": float(a["payoff_if_false"]),
            "expected_value": round(expected, 2),
        })
    ev_rows.sort(key=lambda r: r["expected_value"], reverse=True)
    best = ev_rows[0]
    margin = round(best["expected_value"] - ev_rows[1]["expected_value"], 2) if len(ev_rows) > 1 else 0.0

    # ── 敏感性分析：先验扫描 + 逐条剔除证据 ──
    sensitivity = _sensitivity(prior, evidences, actions, best["name"])

    return {
        "hypothesis": hypothesis,
        "timeframe": timeframe,
        "success_criteria": success_criteria,
        "prior": round(prior, 4),
        "posterior": round(posterior, 4),
        "belief_trace": belief_trace,
        "evidence_count": len(evidences),
        "actions": ev_rows,
        "recommendation": {
            "action": best["name"],
            "expected_value": best["expected_value"],
            "margin_over_runner_up": margin,
            "confidence": _confidence_label(posterior, margin),
        },
        "sensitivity": sensitivity,
        "fragile_evidence": _fragile_evidence(evidences),
        "disclaimer": DISCLAIMER,
    }


def _quick_best(prior: float, evidences: list[dict], actions: list[dict]) -> str:
    lo = _logodds(prior)
    for ev in evidences:
        lo += math.log(_evidence_lr(ev))
    p = _clamp_prob(1 / (1 + math.exp(-lo)))
    best_name, best_ev = "", -1e18
    for a in actions:
        v = p * float(a["payoff_if_true"]) + (1 - p) * float(a["payoff_if_false"])
        if v > best_ev:
            best_name, best_ev = a["name"], v
    return best_name


def _sensitivity(prior: float, evidences: list[dict], actions: list[dict], current_best: str) -> dict:
    flip_below = None
    flip_above = None
    sweep = []
    for p in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        b = _quick_best(p, evidences, actions)
        sweep.append({"prior": p, "best_action": b})
        if p < prior and b != current_best and flip_below is None:
            flip_below = p
        if p > prior and b != current_best and flip_above is None:
            flip_above = p

    critical: list[str] = []
    for i, ev in enumerate(evidences):
        rest = evidences[:i] + evidences[i + 1:]
        if _quick_best(prior, rest, actions) != current_best:
            critical.append(ev.get("name", f"证据{i + 1}"))

    robust = len(critical) == 0
    if robust:
        note = f"剔除任一证据后推荐仍为「{current_best}」，结论对单条证据不敏感，较为稳健。"
    else:
        note = f"剔除 {'、'.join(critical)} 后推荐会改变，结论较依赖该证据，建议优先核实其真实性。"

    return {
        "prior_sweep": sweep,
        "robust": robust,
        "critical_evidence": critical,
        "flip_prior_below": flip_below,
        "flip_prior_above": flip_above,
        "note": note,
    }


def _fragile_evidence(evidences: list[dict]) -> list[dict]:
    """|log LR| 最大的证据 = 当前信念最脆弱的支点。"""
    rows = []
    for ev in evidences:
        lr = _evidence_lr(ev)
        rows.append({
            "evidence": ev.get("name", ""),
            "direction": ev.get("direction", "support"),
            "quality": ev.get("quality", "medium"),
            "lr": round(lr, 3),
            "log_lr": round(abs(math.log(lr)), 3),
        })
    rows.sort(key=lambda r: r["log_lr"], reverse=True)
    return rows[:2]


def _confidence_label(posterior: float, margin: float) -> str:
    if abs(posterior - 0.5) >= 0.25 and margin >= 3.0:
        return "高"
    if abs(posterior - 0.5) >= 0.12 or margin >= 1.5:
        return "中"
    return "低（建议补充证据或选择更保守行动）"
