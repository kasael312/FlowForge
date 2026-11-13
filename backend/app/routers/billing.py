"""Owner-facing billing: plan, usage, invoices with VietQR, PDF download."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.billing import pdf
from app.billing import service as billing
from app.billing.gateways import GATEWAYS
from app.billing.models import Invoice
from app.billing.plans import PAID_PLANS, PLANS, plan_out
from app.db import get_db
from app.workspace import OwnerWorkspace

router = APIRouter(prefix="/biz/billing", tags=["billing"])
DbSession = Annotated[Session, Depends(get_db)]


class InvoiceIn(BaseModel):
    plan: str
    months: int = Field(default=1, ge=1, le=12)
    gateway: str = "bank_transfer"


def _invoice(db: Session, ws_id: str, invoice_id: str) -> Invoice:
    inv = db.get(Invoice, invoice_id)
    if inv is None or inv.workspace_id != ws_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "invoice not found")
    return inv


@router.get("")
def get_billing(ctx: OwnerWorkspace, db: DbSession) -> dict[str, Any]:
    """Current plan + status, this month's usage, the catalogue and any pending invoice."""
    st = billing.state(db, ctx.workspace)
    pending = billing.pending_invoice(db, ctx.id)
    return {
        **st,
        "plans": [plan_out(PLANS[k]) for k in ("trial", *PAID_PLANS)],
        "gateways": [{"key": g.key, "name": g.name} for g in GATEWAYS.values()],
        "pending_invoice": billing.invoice_out(pending) if pending else None,
    }


@router.get("/invoices")
def list_invoices(ctx: OwnerWorkspace, db: DbSession) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(Invoice).where(Invoice.workspace_id == ctx.id).order_by(Invoice.created_at.desc())
    ).all()
    return [billing.invoice_out(i) for i in rows]


@router.post("/invoices", status_code=status.HTTP_201_CREATED)
def create_invoice(body: InvoiceIn, ctx: OwnerWorkspace, db: DbSession) -> dict[str, Any]:
    """Create a payment request; an older pending invoice is cancelled first."""
    if body.plan not in PAID_PLANS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "chỉ mua được gói Cơ bản hoặc Pro"
        )
    if body.gateway not in GATEWAYS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "cổng thanh toán không hỗ trợ")
    old = billing.pending_invoice(db, ctx.id)
    if old is not None:
        billing.cancel_invoice(db, old)
    inv = billing.create_invoice(db, ctx.workspace, body.plan, body.months, body.gateway)
    return billing.invoice_out(inv)


@router.post("/invoices/{invoice_id}/cancel")
def cancel_invoice(invoice_id: str, ctx: OwnerWorkspace, db: DbSession) -> dict[str, Any]:
    inv = _invoice(db, ctx.id, invoice_id)
    try:
        billing.cancel_invoice(db, inv)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return billing.invoice_out(inv)


@router.get("/invoices/{invoice_id}/pdf")
def invoice_pdf(invoice_id: str, ctx: OwnerWorkspace, db: DbSession) -> Response:
    inv = _invoice(db, ctx.id, invoice_id)
    data = pdf.render_invoice(inv, ctx.workspace)
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{inv.number}.pdf"'},
    )
