"""贝叶斯决策路由 — 纯数学引擎，不经过大模型。"""
from __future__ import annotations

from agent.bayesian import DEFAULT_ACTIONS, QUALITY_LR, run_bayesian_decision
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class Evidence(BaseModel):
    name: str
    direction: str = "support"
    quality: str = "medium"
    note: str = ""


class ActionDef(BaseModel):
    name: str
    payoff_if_true: float
    payoff_if_false: float


class DecisionRequest(BaseModel):
    hypothesis: str
    prior: float = 0.5
    timeframe: str = ""
    success_criteria: str = ""
    evidences: list[Evidence]
    actions: list[ActionDef] | None = None


@router.get("/meta")
def meta():
    return {"quality_lr": QUALITY_LR, "default_actions": DEFAULT_ACTIONS}


@router.post("/analyze")
def analyze(req: DecisionRequest):
    result = run_bayesian_decision(
        hypothesis=req.hypothesis,
        prior=req.prior,
        evidences=[e.model_dump() for e in req.evidences],
        actions=[a.model_dump() for a in req.actions] if req.actions else None,
        timeframe=req.timeframe,
        success_criteria=req.success_criteria,
    )
    return {"ok": True, "result": result}
