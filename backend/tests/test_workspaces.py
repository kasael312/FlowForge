"""Workspaces: onboarding, roles, isolation, membership."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.industry import packs
from tests.conftest import login

ANSWERS = {
    "name": "Gym Sức Sống",
    "industry": "gym",
    "branch_count": 2,
    "main_channel": "zalo",
    "current_software": "Excel",
}


def test_onboarding_questions_come_from_packs(client: TestClient) -> None:
    body = client.get("/onboarding/questions").json()
    assert [q["key"] for q in body["questions"]] == [
        "industry",
        "branch_count",
        "main_channel",
        "current_software",
    ]
    assert (
        {o["value"] for o in body["questions"][0]["options"]}
        == set(packs.keys())
        == {"gym", "spa", "both"}
    )
    assert body["questions"][2]["options"][0] == {"value": "zalo", "label": "Zalo OA"}
    assert {i["key"] for i in body["industries"]} == {"gym", "spa", "both"}


def test_onboarding_creates_workspace_and_owner(client: TestClient) -> None:
    assert client.get("/workspaces/current").status_code == 404  # nothing yet
    r = client.post("/onboarding", json=ANSWERS)
    assert r.status_code == 201, r.text
    ws = r.json()
    assert ws["name"] == "Gym Sức Sống" and ws["industry"] == "gym" and ws["role"] == "owner"
    assert ws["industry_label"] == "Phòng gym / fitness" and ws["timezone"] == "Asia/Ho_Chi_Minh"
    assert [b["name"] for b in ws["branches"]] == ["Chi nhánh 1", "Chi nhánh 2"]
    assert ws["onboarding"] == {
        "industry": "gym",
        "branch_count": 2,
        "main_channel": "zalo",
        "current_software": "Excel",
    }
    assert client.get("/workspaces/current").json()["id"] == ws["id"]
    assert client.get("/auth/me").json()["current_workspace_id"] == ws["id"]
    assert client.post("/onboarding", json={**ANSWERS, "industry": "dental"}).status_code == 422


def test_workspace_isolation_and_roles(anon_client: TestClient) -> None:
    login(anon_client, "owner@example.com")
    ws = anon_client.post("/onboarding", json=ANSWERS).json()
    owner_member = anon_client.get("/workspaces/current/members").json()[0]
    assert owner_member["email"] == "owner@example.com" and owner_member["role"] == "owner"

    # a stranger sees nothing and cannot switch into it
    login(anon_client, "stranger@example.com")
    assert anon_client.get("/workspaces").json() == []
    assert anon_client.get("/workspaces/current").status_code == 404
    assert anon_client.post(f"/workspaces/{ws['id']}/switch").status_code == 404

    # owner invites staff (account must exist)
    login(anon_client, "owner@example.com")
    assert (
        anon_client.post(
            "/workspaces/current/members", json={"email": "nobody@example.com"}
        ).status_code
        == 404
    )
    r = anon_client.post(
        "/workspaces/current/members", json={"email": "stranger@example.com", "role": "staff"}
    )
    assert r.status_code == 201 and r.json()["role"] == "staff"
    staff_member_id = r.json()["id"]
    assert (
        anon_client.post(
            "/workspaces/current/members", json={"email": "stranger@example.com"}
        ).status_code
        == 409
    )

    # staff can read but not administer
    login(anon_client, "stranger@example.com")
    assert anon_client.get("/workspaces/current").json()["role"] == "staff"
    assert anon_client.put("/workspaces/current", json={"name": "Hacked"}).status_code == 403
    assert (
        anon_client.post(
            "/workspaces/current/members", json={"email": "owner@example.com"}
        ).status_code
        == 403
    )
    assert anon_client.delete("/workspaces/current").status_code == 403
    assert len(anon_client.get("/workspaces/current/members").json()) == 2

    # owner edits branches and roles; the last owner cannot be demoted/removed
    login(anon_client, "owner@example.com")
    r = anon_client.put(
        "/workspaces/current",
        json={
            "name": "Gym Sức Sống Q1",
            "branches": [{"name": "Quận 1", "address": "1 Lê Lợi", "phone": "0900"}],
        },
    )
    assert (
        r.status_code == 200
        and r.json()["branches"][0]["name"] == "Quận 1"
        and r.json()["branches"][0]["id"]
    )
    assert anon_client.put("/workspaces/current", json={"branches": []}).status_code == 422
    assert (
        anon_client.put(
            f"/workspaces/current/members/{owner_member['id']}", json={"role": "staff"}
        ).status_code
        == 409
    )
    assert (
        anon_client.delete(f"/workspaces/current/members/{owner_member['id']}").status_code == 409
    )
    r = anon_client.put(f"/workspaces/current/members/{staff_member_id}", json={"role": "owner"})
    assert r.json()["role"] == "owner"
    assert (
        anon_client.put(
            f"/workspaces/current/members/{owner_member['id']}", json={"role": "staff"}
        ).status_code
        == 200
    )
    assert anon_client.get("/workspaces/current").json()["role"] == "staff"


def test_multiple_workspaces_and_switch(anon_client: TestClient) -> None:
    login(anon_client, "multi@example.com")
    a = anon_client.post("/onboarding", json=ANSWERS).json()
    b = anon_client.post("/workspaces", json={"name": "Spa Hoa Sen", "industry": "spa"}).json()
    assert b["role"] == "owner" and b["branches"][0]["name"] == "Chi nhánh chính" or b["branches"]
    assert anon_client.get("/workspaces/current").json()["id"] == b["id"]  # creating switches to it
    assert [w["id"] for w in anon_client.get("/workspaces").json()] == [a["id"], b["id"]]
    assert anon_client.post(f"/workspaces/{a['id']}/switch").json()["id"] == a["id"]
    assert anon_client.get("/workspaces/current").json()["name"] == "Gym Sức Sống"
    # deleting the current one falls back to the remaining membership
    assert anon_client.delete("/workspaces/current").status_code == 204
    assert anon_client.get("/workspaces/current").json()["id"] == b["id"]
    assert anon_client.get("/workspaces").json()[0]["id"] == b["id"]


def test_staff_cannot_reach_owner_only_apis(anon_client: TestClient) -> None:
    """Automations, connectors, knowledge base, AI settings and billing are owner-only."""
    login(anon_client, "staff-only@example.com")
    anon_client.post("/auth/logout")
    login(anon_client, "boss@example.com")
    r = anon_client.post(
        "/onboarding",
        json={"name": "Gym X", "industry": "gym", "branch_count": 1, "main_channel": "zalo"},
    )
    assert r.status_code == 201
    assert (
        anon_client.post(
            "/workspaces/current/members", json={"email": "staff-only@example.com", "role": "staff"}
        ).status_code
        == 201
    )
    anon_client.post("/auth/logout")
    login(anon_client, "staff-only@example.com")
    assert anon_client.get("/biz/customers").status_code == 200
    assert anon_client.get("/biz/conversations").status_code == 200
    assert anon_client.get("/biz/overview").status_code == 200
    assert anon_client.get("/biz/notifications").status_code == 200
    for path in (
        "/biz/automations",
        "/biz/connectors",
        "/biz/connectors/meta",
        "/biz/kb/documents",
        "/biz/ai/settings",
        "/biz/billing",
        "/biz/billing/invoices",
    ):
        assert anon_client.get(path).status_code == 403, path
