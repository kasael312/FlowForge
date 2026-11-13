"""Payment gateways. Bank transfer with a VietQR code ships first; others plug in here.

A gateway turns an invoice into payment instructions and, where the provider supports it,
verifies a payment callback. The operator-confirmed bank transfer never auto-verifies: the
system admin matches the transfer description (``transfer_code``) and confirms in the admin panel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import quote

from app.config import get_settings


@dataclass
class PaymentInstructions:
    method: str
    #: Human steps shown to the owner.
    instructions: str
    #: Everything the UI needs (bank name/account, QR image URL, EMV payload, …).
    details: dict[str, Any] = field(default_factory=dict)
    #: ``True`` when payment is confirmed by a human, not by a callback.
    manual_confirmation: bool = True


class PaymentGateway(Protocol):
    key: str
    name: str

    def create_payment(self, invoice: Any) -> PaymentInstructions: ...

    def verify(self, invoice: Any, payload: dict[str, Any]) -> bool:
        """Callback verification for automated providers; manual gateways return ``False``."""
        ...


# ----------------------------------------------------------------- VietQR (NAPAS) payload


def _tlv(tag: str, value: str) -> str:
    return f"{tag}{len(value):02d}{value}"


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return crc


def vietqr_payload(bank_bin: str, account: str, amount: int, add_info: str) -> str:
    """EMVCo payload for a dynamic VietQR transfer (NAPAS 247, account number beneficiary)."""
    merchant = _tlv("00", "A000000727") + _tlv("01", _tlv("00", bank_bin) + _tlv("01", account))
    merchant += _tlv("02", "QRIBFTTA")
    body = (
        _tlv("00", "01")
        + _tlv("01", "12")
        + _tlv("38", merchant)
        + _tlv("53", "704")
        + _tlv("54", str(int(amount)))
        + _tlv("58", "VN")
        + _tlv("62", _tlv("08", add_info[:25]))
        + "6304"
    )
    return body + f"{crc16_ccitt(body.encode('utf-8')):04X}"


class BankTransferGateway:
    """Vietnamese bank transfer: the owner scans a VietQR code; the operator confirms."""

    key = "bank_transfer"
    name = "Chuyển khoản ngân hàng (VietQR)"

    def create_payment(self, invoice: Any) -> PaymentInstructions:
        s = get_settings()
        bank_bin = s.billing_bank_bin.strip()
        account = s.billing_bank_account.strip()
        holder = s.billing_bank_account_name.strip()
        code = invoice.transfer_code
        details: dict[str, Any] = {
            "bank_bin": bank_bin,
            "bank_name": s.billing_bank_name.strip(),
            "account": account,
            "account_name": holder,
            "amount_vnd": invoice.amount_vnd,
            "transfer_code": code,
            "configured": bool(bank_bin and account),
        }
        if bank_bin and account:
            details["qr_payload"] = vietqr_payload(bank_bin, account, invoice.amount_vnd, code)
            details["qr_image_url"] = (
                f"https://img.vietqr.io/image/{bank_bin}-{account}-compact2.png"
                f"?amount={invoice.amount_vnd}&addInfo={quote(code)}&accountName={quote(holder)}"
            )
        instructions = (
            f"Chuyển khoản {invoice.amount_vnd:,} đ".replace(",", ".")
            + f" tới tài khoản {account or '(chưa cấu hình)'}"
            + (f" ({holder})" if holder else "")
            + f" với nội dung đúng mã {code}. Quét mã QR để điền sẵn. "
            "Gói được kích hoạt sau khi quản trị viên xác nhận (thường trong giờ làm việc)."
        )
        return PaymentInstructions(self.key, instructions, details, manual_confirmation=True)

    def verify(self, invoice: Any, payload: dict[str, Any]) -> bool:
        return False


GATEWAYS: dict[str, PaymentGateway] = {BankTransferGateway.key: BankTransferGateway()}


def get_gateway(key: str) -> PaymentGateway:
    try:
        return GATEWAYS[key]
    except KeyError as exc:
        raise ValueError(f"unknown payment gateway {key!r}") from exc
