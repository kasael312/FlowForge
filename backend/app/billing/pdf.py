"""Invoice PDF (fpdf2). Uses DejaVu Sans when installed so Vietnamese renders; else ASCII-folds."""

from __future__ import annotations

import unicodedata
from datetime import UTC
from pathlib import Path

from fpdf import FPDF

from app.billing.models import Invoice
from app.billing.plans import get_plan
from app.models import Workspace

FONT_CANDIDATES = [
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/TTF/DejaVuSans.ttf"),
]
STATUS_VI = {"pending": "Chờ thanh toán", "paid": "Đã thanh toán", "cancelled": "Đã huỷ"}


def _fold(text: str) -> str:
    out = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    return (
        "".join(ch for ch in out if not unicodedata.combining(ch))
        .encode("latin-1", "replace")
        .decode("latin-1")
    )


def _vnd(n: int) -> str:
    return f"{n:,}".replace(",", ".") + " đ"


def render_invoice(inv: Invoice, ws: Workspace, issuer: str = "FlowForge") -> bytes:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    font = next((p for p in FONT_CANDIDATES if p.exists()), None)
    if font is not None:
        pdf.add_font("ui", "", str(font))
        bold = font.with_name("DejaVuSans-Bold.ttf")
        pdf.add_font("ui", "B", str(bold if bold.exists() else font))
        family, t = "ui", (lambda s: s)
    else:
        family, t = "helvetica", _fold

    plan = get_plan(inv.plan)
    created = (
        inv.created_at.replace(tzinfo=UTC) if inv.created_at.tzinfo is None else inv.created_at
    )
    pdf.set_font(family, "B", 18)
    pdf.cell(0, 10, t(f"HOÁ ĐƠN {inv.number}"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(family, "", 10)
    pdf.cell(0, 6, t(f"Bên cung cấp: {issuer}"), new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, t(f"Ngày lập: {created:%d/%m/%Y}"), new_x="LMARGIN", new_y="NEXT")
    pdf.cell(
        0,
        6,
        t(f"Trạng thái: {STATUS_VI.get(inv.status.value, inv.status.value)}"),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(4)
    pdf.set_font(family, "B", 11)
    pdf.cell(0, 7, t("Khách hàng"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(family, "", 10)
    pdf.cell(0, 6, t(f"Cơ sở: {ws.name}"), new_x="LMARGIN", new_y="NEXT")
    branch = (ws.branches or [{}])[0]
    if branch.get("address"):
        pdf.cell(0, 6, t(f"Địa chỉ: {branch['address']}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    pdf.set_font(family, "B", 10)
    pdf.set_fill_color(235, 235, 235)
    pdf.cell(100, 8, t("Mô tả"), border=1, fill=True)
    pdf.cell(30, 8, t("Số tháng"), border=1, fill=True, align="C")
    pdf.cell(60, 8, t("Thành tiền"), border=1, fill=True, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(family, "", 10)
    pdf.cell(100, 8, t(f"Gói {plan.name} FlowForge ({_vnd(plan.price_vnd)}/tháng)"), border=1)
    pdf.cell(30, 8, str(inv.months), border=1, align="C")
    pdf.cell(60, 8, t(_vnd(inv.amount_vnd)), border=1, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(family, "B", 11)
    pdf.cell(130, 9, t("Tổng cộng"), border=1)
    pdf.cell(60, 9, t(_vnd(inv.amount_vnd)), border=1, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    pay = inv.payment or {}
    pdf.set_font(family, "B", 11)
    pdf.cell(0, 7, t("Thanh toán"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(family, "", 10)
    if pay.get("account"):
        pdf.cell(
            0,
            6,
            t(f"Ngân hàng: {pay.get('bank_name') or pay.get('bank_bin', '')}"),
            new_x="LMARGIN",
            new_y="NEXT",
        )
        pdf.cell(
            0,
            6,
            t(f"Số tài khoản: {pay['account']} ({pay.get('account_name', '')})"),
            new_x="LMARGIN",
            new_y="NEXT",
        )
    pdf.cell(0, 6, t(f"Nội dung chuyển khoản: {inv.transfer_code}"), new_x="LMARGIN", new_y="NEXT")
    if inv.paid_at:
        paid = inv.paid_at.replace(tzinfo=UTC) if inv.paid_at.tzinfo is None else inv.paid_at
        pdf.cell(0, 6, t(f"Đã nhận thanh toán ngày {paid:%d/%m/%Y}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)
    pdf.set_font(family, "", 8)
    pdf.set_text_color(110, 110, 110)
    pdf.multi_cell(
        0,
        5,
        t(
            "Hoá đơn được tạo tự động bởi FlowForge. Gói được kích hoạt ngay khi quản trị viên xác nhận giao dịch."
        ),
    )
    return bytes(pdf.output())
