"""The "Gym & Spa" industry packs (gym, spa, both)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.biz.automations import seed_flows
from app.biz.sample import seed_sample
from app.industry.base import IndustryPack, packs
from app.models import Workspace


class GymSpaPack(IndustryPack):
    """Shared behaviour; sample data and flow enabling arrive with the data and flow parts."""

    def on_onboarded(self, db: Session, workspace: Workspace, answers: dict[str, Any]) -> None:
        seed_sample(db, workspace)  # services, staff, customers, memberships, appointments, inbox
        owner = str(answers.get("owner_user_id") or "")
        if owner:
            seed_flows(db, workspace, owner)  # the six "Việc tự động" cards


GYM = packs.register(
    GymSpaPack(
        key="gym",
        label="Phòng gym / fitness",
        description="Gói tập, PT, check-in, nhắc gia hạn và chăm khách lâu không tới.",
    )
)
SPA = packs.register(
    GymSpaPack(
        key="spa",
        label="Spa / thẩm mỹ",
        description="Liệu trình, đặt lịch kỹ thuật viên, nhắc lịch và xin đánh giá.",
    )
)
BOTH = packs.register(
    GymSpaPack(
        key="both",
        label="Cả gym và spa",
        description="Dùng chung dữ liệu khách hàng, lịch và tin nhắn cho cả hai mảng.",
    )
)
