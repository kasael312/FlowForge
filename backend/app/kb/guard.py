"""Safety rail: questions about health, injuries, drugs or clinical nutrition go to a human.

Keyword/phrase based on accent-folded text so it is cheap, deterministic and
testable; the owner can add their own handoff keywords on top.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.kb.embed import fold

CATEGORIES: dict[str, list[str]] = {
    "y tế / bệnh lý": [
        "benh",
        "bac si",
        "chan doan",
        "dieu tri",
        "trieu chung",
        "huyet ap",
        "tieu duong",
        "tim mach",
        "mang thai",
        "co thai",
        "sau sinh",
        "hen suyen",
        "dong kinh",
        "ung thu",
        "di ung",
        "benh ly",
        "phau thuat",
        "ca mo",
        "mo xe",
        "xet nghiem",
        "nhiem trung",
        "viem khop",
        "viem gan",
        "bi viem",
    ],
    "chấn thương": [
        "chan thuong",
        "dau lung",
        "dau goi",
        "dau vai",
        "dau co",
        "dau khop",
        "thoat vi",
        "dia dem",
        "gay xuong",
        "bong gan",
        "trat khop",
        "rach co",
        "dau nhuc",
        "bi dau",
        "te tay",
        "te chan",
        "dau that lung",
        "bi sung",
        "sung tay",
        "sung chan",
    ],
    "thuốc / thực phẩm chức năng": [
        "thuoc",
        "uong thuoc",
        "ke don",
        "steroid",
        "whey",
        "creatine",
        "pre workout",
        "pre-workout",
        "bcaa",
        "thuoc giam can",
        "thuc pham chuc nang",
        "tpcn",
        "vien uong",
        "mui tiem",
        "tiem thuoc",
        "hormone",
    ],
    "dinh dưỡng chuyên sâu": [
        "thuc don",
        "che do an",
        "an kieng",
        "keto",
        "nhin an",
        "calo",
        "calories",
        "macro",
        "giam can bao nhieu kg",
        "tang can",
        "an gi de",
        "bo sung dinh duong",
        "dinh duong",
    ],
}


@dataclass
class GuardDecision:
    handoff: bool
    category: str | None = None
    matched: str | None = None


def check(
    text: str, extra_keywords: list[str] | None = None, enabled: bool = True
) -> GuardDecision:
    """Decide whether ``text`` must be handed to staff instead of answered by the AI."""
    folded = " " + re.sub(r"\s+", " ", fold(text)) + " "
    for kw in extra_keywords or []:
        k = fold(kw).strip()
        if k and k in folded:
            return GuardDecision(True, "từ khoá chủ cơ sở đặt", kw)
    if not enabled:
        return GuardDecision(False)
    for category, words in CATEGORIES.items():
        for w in words:
            if w in folded:
                return GuardDecision(True, category, w.strip())
    return GuardDecision(False)
