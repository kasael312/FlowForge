"""CSV / Excel import with column mapping and preview for customers, memberships, services."""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.biz import service as biz
from app.biz.models import Customer, Membership, MembershipStatus, Service, ServiceCategory
from app.biz.phone import normalize_phone

MAX_ROWS = 5000


class ImportError_(ValueError):
    """Unreadable file or bad mapping."""


# ---------------------------------------------------------------- field specs

FIELDS: dict[str, list[dict[str, Any]]] = {
    "customers": [
        {"key": "name", "label": "Tên khách", "required": True},
        {"key": "phone", "label": "Số điện thoại", "required": False},
        {"key": "zalo_id", "label": "Zalo ID", "required": False},
        {"key": "fb_psid", "label": "Facebook PSID", "required": False},
        {"key": "tags", "label": "Nhãn (phẩy)", "required": False},
        {"key": "source", "label": "Nguồn", "required": False},
        {"key": "notes", "label": "Ghi chú", "required": False},
        {"key": "last_visit_at", "label": "Lần đến gần nhất", "required": False},
    ],
    "memberships": [
        {"key": "phone", "label": "SĐT khách (để tìm khách)", "required": True},
        {"key": "customer_name", "label": "Tên khách (tạo mới nếu chưa có)", "required": False},
        {"key": "service_name", "label": "Tên gói / dịch vụ", "required": True},
        {"key": "start_date", "label": "Ngày bắt đầu", "required": True},
        {"key": "end_date", "label": "Ngày kết thúc", "required": False},
        {"key": "sessions_total", "label": "Tổng số buổi", "required": False},
        {"key": "sessions_used", "label": "Số buổi đã dùng", "required": False},
        {"key": "amount_paid", "label": "Số tiền đã trả", "required": False},
        {"key": "status", "label": "Trạng thái (active/expired/frozen)", "required": False},
    ],
    "services": [
        {"key": "name", "label": "Tên gói / dịch vụ", "required": True},
        {
            "key": "category",
            "label": "Loại (gym_package/pt_session/spa_treatment/course)",
            "required": False,
        },
        {"key": "duration_min", "label": "Thời lượng (phút)", "required": False},
        {"key": "price", "label": "Giá", "required": False},
        {"key": "sessions", "label": "Số buổi", "required": False},
        {"key": "validity_days", "label": "Hiệu lực (ngày)", "required": False},
        {"key": "description", "label": "Mô tả", "required": False},
        {"key": "sale_notes", "label": "Ghi chú cho AI", "required": False},
    ],
}

# header synonyms (lower-cased, accents stripped) → field key
SYNONYMS: dict[str, list[str]] = {
    "name": [
        "ten",
        "ten khach",
        "ho ten",
        "khach hang",
        "name",
        "customer",
        "full name",
        "ten goi",
        "ten dich vu",
        "dich vu",
        "goi",
        "service",
    ],
    "customer_name": ["ten khach", "ho ten", "khach hang", "customer", "name"],
    "phone": ["sdt", "so dien thoai", "dien thoai", "phone", "mobile", "tel", "so dt"],
    "zalo_id": ["zalo", "zalo id"],
    "fb_psid": ["facebook", "fb", "psid", "fb psid"],
    "tags": ["nhan", "tag", "tags", "the"],
    "source": ["nguon", "source", "kenh"],
    "notes": ["ghi chu", "note", "notes", "mo ta"],
    "last_visit_at": ["lan den gan nhat", "last visit", "ngay den"],
    "service_name": ["goi", "ten goi", "dich vu", "service", "goi tap", "lieu trinh", "package"],
    "start_date": ["ngay bat dau", "bat dau", "start", "start date", "tu ngay"],
    "end_date": ["ngay ket thuc", "ket thuc", "het han", "end", "end date", "den ngay", "expiry"],
    "sessions_total": ["so buoi", "tong buoi", "tong so buoi", "sessions", "buoi"],
    "sessions_used": ["da dung", "buoi da dung", "used", "da tap"],
    "amount_paid": ["so tien", "da tra", "thanh toan", "amount", "paid", "tien"],
    "status": ["trang thai", "status"],
    "category": ["loai", "category", "phan loai", "nhom"],
    "duration_min": ["thoi luong", "phut", "duration", "minutes"],
    "price": ["gia", "price", "don gia", "gia ban"],
    "sessions": ["so buoi", "sessions", "buoi"],
    "validity_days": ["hieu luc", "so ngay", "validity", "days"],
    "description": ["mo ta", "description"],
    "sale_notes": ["ghi chu ai", "sale notes", "ghi chu ban hang", "ghi chu"],
}

_ACCENTS = str.maketrans(
    "àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ",
    "aaaaaaaaaaaaaaaaaeeeeeeeeeeeiiiiiooooooooooooooooouuuuuuuuuuuyyyyyd",
)


def fold(s: str) -> str:
    s = s.strip().lower().translate(_ACCENTS)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def suggest_mapping(entity: str, columns: list[str]) -> dict[str, str]:
    folded = {c: fold(c) for c in columns}
    used: set[str] = set()
    mapping: dict[str, str] = {}
    for f in FIELDS[entity]:
        key = f["key"]
        candidates = [key.replace("_", " "), *SYNONYMS.get(key, [])]
        best = None
        for col, fc in folded.items():
            if col in used:
                continue
            if fc in candidates:
                best = col
                break
        if best is None:
            for col, fc in folded.items():
                if col in used:
                    continue
                if any(c in fc for c in candidates if len(c) >= 3):
                    best = col
                    break
        if best is not None:
            mapping[key] = best
            used.add(best)
    return mapping


# ---------------------------------------------------------------- parsing


def parse_table(data: bytes, filename: str) -> tuple[list[str], list[dict[str, Any]]]:
    """Columns + rows (dicts of str) from CSV or XLSX bytes."""
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        return _parse_xlsx(data)
    return _parse_csv(data)


def _parse_csv(data: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    text = data.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    rows_raw = [r for r in reader if any(cell.strip() for cell in r)]
    if not rows_raw:
        raise ImportError_("file is empty")
    columns = [c.strip() or f"col{i + 1}" for i, c in enumerate(rows_raw[0])]
    rows = []
    for r in rows_raw[1 : MAX_ROWS + 1]:
        rows.append({columns[i]: (r[i].strip() if i < len(r) else "") for i in range(len(columns))})
    return columns, rows


def _parse_xlsx(data: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover
        raise ImportError_("openpyxl is not installed") from exc
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise ImportError_("cannot read the Excel file") from exc
    ws = wb.active
    if ws is None:
        raise ImportError_("workbook has no sheets")
    it = ws.iter_rows(values_only=True)
    header = next(it, None)
    if not header:
        raise ImportError_("sheet is empty")
    columns = [
        str(c).strip() if c is not None and str(c).strip() else f"col{i + 1}"
        for i, c in enumerate(header)
    ]
    rows = []
    for r in it:
        if r is None or not any(c is not None and str(c).strip() for c in r):
            continue
        rows.append({columns[i]: _cell(r[i]) if i < len(r) else "" for i in range(len(columns))})
        if len(rows) >= MAX_ROWS:
            break
    return columns, rows


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.date().isoformat() if v.time() == datetime.min.time() else v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


# ---------------------------------------------------------------- value coercion

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%y", "%m/%d/%Y")


def parse_date(value: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value[:10], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def parse_number(value: str) -> float | None:
    s = re.sub(r"[^0-9,.\-]", "", str(value or ""))
    if not s:
        return None
    if s.count(",") and s.count("."):
        s = (
            s.replace(".", "").replace(",", ".")
            if s.rfind(",") > s.rfind(".")
            else s.replace(",", "")
        )
    elif s.count(","):
        parts = s.split(",")
        s = s.replace(",", "") if len(parts[-1]) == 3 and len(parts) > 1 else s.replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def _val(row: dict[str, Any], mapping: dict[str, str], key: str) -> str:
    col = mapping.get(key)
    return str(row.get(col, "") or "").strip() if col else ""


# ---------------------------------------------------------------- apply


def apply_import(
    db: Session,
    workspace_id: str,
    entity: str,
    rows: list[dict[str, Any]],
    mapping: dict[str, str],
) -> dict[str, Any]:
    if entity not in FIELDS:
        raise ImportError_(f"unknown entity {entity!r}")
    required = [f["key"] for f in FIELDS[entity] if f["required"]]
    missing = [k for k in required if not mapping.get(k)]
    if missing:
        raise ImportError_(f"map these required fields first: {', '.join(missing)}")
    created = updated = skipped = 0
    errors: list[str] = []
    for i, row in enumerate(rows, start=2):
        try:
            if entity == "customers":
                outcome = _import_customer(db, workspace_id, row, mapping)
            elif entity == "services":
                outcome = _import_service(db, workspace_id, row, mapping)
            else:
                outcome = _import_membership(db, workspace_id, row, mapping)
        except ValueError as exc:
            errors.append(f"dòng {i}: {exc}")
            skipped += 1
            continue
        if outcome == "created":
            created += 1
        elif outcome == "updated":
            updated += 1
        else:
            skipped += 1
        if len(errors) >= 50:
            errors.append("… dừng ghi lỗi sau 50 dòng")
            break
    db.commit()
    return {
        "entity": entity,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
    }


def _tags(value: str) -> list[str]:
    return [t.strip() for t in re.split(r"[,;|]", value) if t.strip()][:30]


def _import_customer(db: Session, ws: str, row: dict[str, Any], m: dict[str, str]) -> str:
    name = _val(row, m, "name")
    if not name:
        raise ValueError("thiếu tên")
    phone = normalize_phone(_val(row, m, "phone"))
    existing = biz.find_by_phone(db, ws, phone) if phone else None
    c = existing or Customer(workspace_id=ws, name=name, source="import")
    c.name = name
    if phone:
        biz.set_phone(c, phone)
    for key in ("zalo_id", "fb_psid"):
        v = _val(row, m, key)
        if v:
            setattr(c, key, v)
    tags = _tags(_val(row, m, "tags"))
    if tags:
        c.tags = sorted(set(list(c.tags or []) + tags))
    src = _val(row, m, "source")
    if src:
        c.source = src[:50]
    notes = _val(row, m, "notes")
    if notes:
        c.notes = notes[:5000]
    lv = parse_date(_val(row, m, "last_visit_at"))
    if lv:
        c.last_visit_at = datetime.combine(lv, datetime.min.time()).replace(
            tzinfo=__import__("datetime").UTC
        )
    if existing is None:
        db.add(c)
        db.flush()
        return "created"
    return "updated"


def _import_service(db: Session, ws: str, row: dict[str, Any], m: dict[str, str]) -> str:
    name = _val(row, m, "name")
    if not name:
        raise ValueError("thiếu tên")
    existing = db.scalar(select(Service).where(Service.workspace_id == ws, Service.name == name))
    s = existing or Service(workspace_id=ws, name=name, category=ServiceCategory.gym_package)
    cat = _val(row, m, "category").lower()
    if cat:
        try:
            s.category = ServiceCategory(cat)
        except ValueError as exc:
            raise ValueError(f"loại không hợp lệ: {cat}") from exc
    for key in ("duration_min", "sessions", "validity_days"):
        v = parse_number(_val(row, m, key))
        if v is not None:
            setattr(s, key, int(v))
    price = parse_number(_val(row, m, "price"))
    if price is not None:
        s.price = price
    for key in ("description", "sale_notes"):
        v = _val(row, m, key)
        if v:
            setattr(s, key, v[:5000])
    if existing is None:
        db.add(s)
        db.flush()
        return "created"
    return "updated"


def _import_membership(db: Session, ws: str, row: dict[str, Any], m: dict[str, str]) -> str:
    phone = normalize_phone(_val(row, m, "phone"))
    if not phone:
        raise ValueError("thiếu số điện thoại khách")
    customer = biz.find_by_phone(db, ws, phone)
    if customer is None:
        cname = _val(row, m, "customer_name")
        if not cname:
            raise ValueError(f"không tìm thấy khách có SĐT {phone} (thêm cột tên khách để tạo mới)")
        customer = Customer(workspace_id=ws, name=cname, source="import")
        biz.set_phone(customer, phone)
        db.add(customer)
        db.flush()
    sname = _val(row, m, "service_name")
    svc = db.scalar(select(Service).where(Service.workspace_id == ws, Service.name == sname))
    if svc is None:
        raise ValueError(f"không có gói/dịch vụ tên '{sname}' (tạo gói trước hoặc import dịch vụ)")
    start = parse_date(_val(row, m, "start_date"))
    if start is None:
        raise ValueError("ngày bắt đầu không hợp lệ")
    mem = Membership(
        workspace_id=ws,
        customer_id=customer.id,
        service_id=svc.id,
        start_date=start,
        end_date=parse_date(_val(row, m, "end_date")),
        sessions_total=int(parse_number(_val(row, m, "sessions_total")) or 0),
        sessions_used=int(parse_number(_val(row, m, "sessions_used")) or 0),
        amount_paid=float(parse_number(_val(row, m, "amount_paid")) or 0),
    )
    status = _val(row, m, "status").lower()
    if status == "frozen":
        mem.status = MembershipStatus.frozen
    biz.apply_service_defaults(mem, svc)
    biz.refresh_status(mem)
    db.add(mem)
    db.flush()
    return "created"
