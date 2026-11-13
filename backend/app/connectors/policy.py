"""Messaging-window rules shared by Zalo OA and Facebook Messenger.

Both platforms allow free-form replies only inside 24 hours after the customer's
last message. Outside that window Zalo needs a ZNS template (paid, sent to the
phone number) and Messenger needs a *message tag* from Meta's allowed list.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

WINDOW = timedelta(hours=24)

#: Meta message tags usable outside the 24h window (and what they may be used for).
FB_TAGS: dict[str, str] = {
    "CONFIRMED_EVENT_UPDATE": "Nhắc / cập nhật lịch hẹn khách đã đặt",
    "POST_PURCHASE_UPDATE": "Cập nhật sau mua hàng (gói, thanh toán, hoá đơn)",
    "ACCOUNT_UPDATE": "Thay đổi tài khoản / gói thành viên",
    "HUMAN_AGENT": "Nhân viên trả lời trong 7 ngày",
}


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def within_window(last_inbound_at: datetime | None, now: datetime | None = None) -> bool:
    """True when a free-form reply is still allowed (customer wrote < 24h ago)."""
    last = _aware(last_inbound_at)
    if last is None:
        return False
    now = now or datetime.now(UTC)
    return now - last < WINDOW


def window_closes_at(last_inbound_at: datetime | None) -> datetime | None:
    last = _aware(last_inbound_at)
    return last + WINDOW if last else None


def fb_tag_allowed(tag: str | None) -> bool:
    return bool(tag) and tag in FB_TAGS
