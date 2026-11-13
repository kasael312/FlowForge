"""Google Calendar (optional): two-way sync of appointments with one calendar.

Secrets: ``client_id``, ``client_secret``, ``refresh_token`` (OAuth, scope
``https://www.googleapis.com/auth/calendar``). Config: ``calendar_id`` (default
``primary``), ``sync_token`` (incremental pull), ``calendar_name``.

Push: every booked/confirmed appointment becomes an event (id kept in
``Appointment.google_event_id``); cancellations delete it. Pull: events changed in
Google move/cancel the matching appointment; new events created directly in
Google become appointments for a placeholder customer so staff see them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.biz import service as biz
from app.biz.models import Appointment, AppointmentSource, AppointmentStatus, Customer, Staff
from app.connectors.base import ConnectorError, TestResult, client, registry, secrets_of
from app.connectors.models import Connector

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/calendar/v3"
SYNC_STATUSES = (
    AppointmentStatus.booked,
    AppointmentStatus.confirmed,
    AppointmentStatus.arrived,
    AppointmentStatus.done,
)


async def _access_token(c: Connector) -> str:
    s = secrets_of(c)
    if not all(s.get(k) for k in ("client_id", "client_secret", "refresh_token")):
        raise ConnectorError("Thiếu client_id / client_secret / refresh_token của Google.")
    async with client() as http:
        r = await http.post(
            TOKEN_URL,
            data={
                "client_id": s["client_id"],
                "client_secret": s["client_secret"],
                "refresh_token": s["refresh_token"],
                "grant_type": "refresh_token",
            },
        )
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or not data.get("access_token"):
        raise ConnectorError(
            f"Google từ chối refresh token: {data.get('error_description') or data.get('error') or r.status_code}"
        )
    return str(data["access_token"])


async def _api(c: Connector, method: str, path: str, **kw: Any) -> dict[str, Any]:
    token = await _access_token(c)
    async with client() as http:
        r = await http.request(
            method, f"{API}{path}", headers={"Authorization": f"Bearer {token}"}, **kw
        )
    if r.status_code == 204 or not r.content:
        return {}
    try:
        data = r.json()
    except ValueError as exc:
        raise ConnectorError(f"Google trả về dữ liệu không hợp lệ (HTTP {r.status_code})") from exc
    if r.status_code >= 400:
        err = data.get("error") or {}
        raise ConnectorError(
            f"Google Calendar lỗi {r.status_code}: {err.get('message') if isinstance(err, dict) else err}"
        )
    return data


def _calendar_id(c: Connector) -> str:
    return str((c.config or {}).get("calendar_id") or "primary")


async def test_connection(c: Connector) -> TestResult:
    try:
        cal = await _api(c, "GET", f"/calendars/{_calendar_id(c)}")
    except (ConnectorError, httpx.HTTPError) as exc:
        return TestResult(False, f"Không kết nối được Google Calendar: {exc}")
    cfg = dict(c.config or {})
    cfg["calendar_name"] = str(cal.get("summary") or "")
    c.config = cfg
    return TestResult(
        True, f"Đã kết nối lịch “{cfg['calendar_name']}”.", {"timezone": cal.get("timeZone")}
    )


def _event_body(db: Session, a: Appointment, tz: str) -> dict[str, Any]:
    cust = db.get(Customer, a.customer_id)
    staff = db.get(Staff, a.staff_id) if a.staff_id else None
    title = f"{cust.name if cust else 'Khách'} – {a.service.name if a.service else 'Lịch hẹn'}"
    return {
        "summary": title,
        "description": (f"Nhân viên: {staff.name}\n" if staff else "") + (a.notes or ""),
        "start": {"dateTime": biz.aware(a.start_at).isoformat(), "timeZone": tz},
        "end": {"dateTime": biz.aware(a.end_at).isoformat(), "timeZone": tz},
        "extendedProperties": {"private": {"flowforge_appointment": a.id}},
    }


async def push_appointment(db: Session, c: Connector, a: Appointment, tz: str) -> None:
    """Create / update / delete the Google event mirroring ``a`` (no-op when disabled)."""
    if not c.enabled:
        return
    cal = _calendar_id(c)
    if a.status in SYNC_STATUSES:
        body = _event_body(db, a, tz)
        if a.google_event_id:
            await _api(c, "PUT", f"/calendars/{cal}/events/{a.google_event_id}", json=body)
        else:
            created = await _api(c, "POST", f"/calendars/{cal}/events", json=body)
            a.google_event_id = str(created.get("id") or "") or None
    elif a.google_event_id:
        try:
            await _api(c, "DELETE", f"/calendars/{cal}/events/{a.google_event_id}")
        except ConnectorError as exc:
            if "410" not in str(exc) and "404" not in str(exc):
                raise
        a.google_event_id = None


async def pull_events(db: Session, c: Connector, workspace_id: str, tz: str) -> dict[str, int]:
    """Apply changes made in Google (moved / cancelled / new events) to appointments."""
    cal = _calendar_id(c)
    cfg = dict(c.config or {})
    params: dict[str, Any] = {"singleEvents": "true", "maxResults": 250}
    if cfg.get("sync_token"):
        params["syncToken"] = cfg["sync_token"]
    else:
        params["timeMin"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    moved = cancelled = created = 0
    page_token: str | None = None
    while True:
        if page_token:
            params["pageToken"] = page_token
        try:
            data = await _api(c, "GET", f"/calendars/{cal}/events", params=params)
        except ConnectorError as exc:
            if "410" in str(exc) and "syncToken" in params:  # token expired → full resync
                params.pop("syncToken")
                params["timeMin"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()
                continue
            raise
        for ev in data.get("items") or []:
            ours = ((ev.get("extendedProperties") or {}).get("private") or {}).get(
                "flowforge_appointment"
            )
            appt = (
                db.get(Appointment, ours)
                if ours
                else db.scalar(
                    select(Appointment).where(
                        Appointment.workspace_id == workspace_id,
                        Appointment.google_event_id == ev.get("id"),
                    )
                )
            )
            if appt is not None and appt.workspace_id != workspace_id:
                appt = None
            if ev.get("status") == "cancelled":
                if appt is not None and appt.status in biz.BLOCKING:
                    appt.status = AppointmentStatus.cancelled
                    cancelled += 1
                continue
            start = (ev.get("start") or {}).get("dateTime")
            end = (ev.get("end") or {}).get("dateTime")
            if not start or not end:
                continue  # all-day events are not appointments
            s, e = (
                datetime.fromisoformat(start).astimezone(UTC),
                datetime.fromisoformat(end).astimezone(UTC),
            )
            if appt is not None:
                if biz.aware(appt.start_at) != s or biz.aware(appt.end_at) != e:
                    appt.start_at, appt.end_at = s, e
                    moved += 1
                continue
            # created directly in Google: placeholder customer so the slot is blocked and visible
            name = f"Google: {ev.get('summary') or 'Sự kiện'}"[:200]
            cust = db.scalar(
                select(Customer).where(Customer.workspace_id == workspace_id, Customer.name == name)
            )
            if cust is None:
                cust = Customer(
                    workspace_id=workspace_id, name=name, source="google_calendar", tags=["google"]
                )
                db.add(cust)
                db.flush()
            db.add(
                Appointment(
                    workspace_id=workspace_id,
                    customer_id=cust.id,
                    start_at=s,
                    end_at=e,
                    status=AppointmentStatus.booked,
                    source=AppointmentSource.manual,
                    google_event_id=str(ev.get("id") or ""),
                    notes=str(ev.get("description") or "")[:2000],
                )
            )
            created += 1
        page_token = data.get("nextPageToken")
        if not page_token:
            if data.get("nextSyncToken"):
                cfg["sync_token"] = data["nextSyncToken"]
                c.config = cfg
            break
    db.commit()
    return {"moved": moved, "cancelled": cancelled, "created": created}


registry["google_calendar"] = lambda: {"test": test_connection}
