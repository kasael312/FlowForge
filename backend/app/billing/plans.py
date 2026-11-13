"""The plan catalogue: trial → basic → pro. Limits are per calendar month / per workspace."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Plan:
    key: str
    name: str
    price_vnd: int  # per month, 0 for the trial
    ai_conversations_month: int
    branches: int
    description: str
    #: Trial only: how many days it lasts.
    trial_days: int = 0


PLANS: dict[str, Plan] = {
    "trial": Plan(
        "trial",
        "Dùng thử",
        0,
        100,
        5,
        "30 ngày miễn phí, đủ mọi tính năng, 100 hội thoại AI.",
        trial_days=30,
    ),
    "basic": Plan(
        "basic",
        "Cơ bản",
        499_000,
        500,
        1,
        "1 chi nhánh, 500 hội thoại AI mỗi tháng, mọi việc tự động.",
    ),
    "pro": Plan(
        "pro",
        "Pro",
        1_290_000,
        3_000,
        5,
        "Tối đa 5 chi nhánh, 3.000 hội thoại AI mỗi tháng, ưu tiên hỗ trợ.",
    ),
}
PAID_PLANS = ("basic", "pro")
#: Days a paid subscription keeps working after its period ends (while a transfer clears).
GRACE_DAYS = 7


def get_plan(key: str) -> Plan:
    return PLANS.get(key) or PLANS["trial"]


def plan_out(p: Plan) -> dict[str, object]:
    return {
        "key": p.key,
        "name": p.name,
        "price_vnd": p.price_vnd,
        "ai_conversations_month": p.ai_conversations_month,
        "branches": p.branches,
        "description": p.description,
        "trial_days": p.trial_days,
    }
