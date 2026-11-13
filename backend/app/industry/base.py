"""Industry packs: everything vertical-specific lives behind this interface.

A pack describes one industry (gym, spa, …): the onboarding questions, what to
seed when a workspace is created, and (later) the flows shown as on/off cards.
Core code never branches on the industry; it asks the pack.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.models import Workspace

QuestionType = Literal["select", "number", "text"]


@dataclass(frozen=True)
class Question:
    key: str
    label: str
    type: QuestionType = "text"
    options: tuple[tuple[str, str], ...] = ()  # (value, label)
    default: Any = None
    required: bool = True
    placeholder: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "type": self.type,
            "options": [{"value": v, "label": lbl} for v, lbl in self.options],
            "default": self.default,
            "required": self.required,
            "placeholder": self.placeholder,
        }


@dataclass
class IndustryPack:
    key: str
    label: str
    description: str = ""
    timezone: str = "Asia/Ho_Chi_Minh"
    #: Extra onboarding questions after the common ones.
    questions: list[Question] = field(default_factory=list)
    #: Keys of seeded industry flows to enable right after onboarding (part 5).
    default_flows: list[str] = field(default_factory=list)

    def on_onboarded(self, db: Session, workspace: Workspace, answers: dict[str, Any]) -> None:
        """Hook: seed sample data / enable flows. Default does nothing."""


# Questions every workspace answers; the industry question is built from the registry.
COMMON_QUESTIONS: list[Question] = [
    Question(
        key="branch_count",
        label="Bạn có bao nhiêu chi nhánh?",
        type="number",
        default=1,
        placeholder="1",
    ),
    Question(
        key="main_channel",
        label="Khách hàng nhắn tin cho bạn chủ yếu qua kênh nào?",
        type="select",
        options=(("zalo", "Zalo OA"), ("facebook", "Facebook Messenger"), ("both", "Cả hai")),
        default="zalo",
    ),
    Question(
        key="current_software",
        label="Bạn đang dùng phần mềm quản lý nào? (để trống nếu chưa có)",
        type="text",
        required=False,
        placeholder="Ví dụ: Excel, KiotViet, phần mềm riêng…",
    ),
]


class PackRegistry:
    def __init__(self) -> None:
        self._packs: dict[str, IndustryPack] = {}

    def register(self, pack: IndustryPack) -> IndustryPack:
        self._packs[pack.key] = pack
        return pack

    def get(self, key: str) -> IndustryPack:
        try:
            return self._packs[key]
        except KeyError as exc:
            raise KeyError(f"unknown industry {key!r}") from exc

    def keys(self) -> list[str]:
        return list(self._packs)

    def __contains__(self, key: object) -> bool:
        return key in self._packs

    def all(self) -> list[IndustryPack]:
        return list(self._packs.values())

    def questions(self) -> list[dict[str, Any]]:
        """Onboarding questions: industry first, then the common ones."""
        industry = Question(
            key="industry",
            label="Bạn kinh doanh loại hình nào?",
            type="select",
            options=tuple((p.key, p.label) for p in self.all()),
            default=self.keys()[0] if self._packs else None,
        )
        return [industry.to_dict(), *(q.to_dict() for q in COMMON_QUESTIONS)]


packs = PackRegistry()
