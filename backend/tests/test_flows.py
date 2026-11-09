"""Tests for the /flows CRUD endpoints and graph validation."""

from fastapi.testclient import TestClient


def test_create_and_get_flow(client: TestClient, sample_flow: dict) -> None:
    created = client.post("/flows", json=sample_flow)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "Fix issue"
    assert len(body["nodes"]) == 5
    # defaults are filled in
    plan = next(n for n in body["nodes"] if n["id"] == "plan")
    assert plan["data"]["on_fail"] == "stop"
    assert plan["data"]["max_turns"] == 20
    assert set(plan["data"]["tools_allowed"]) == {
        "read_file",
        "write_file",
        "run_shell",
        "list_files",
        "git_diff",
    }

    fetched = client.get(f"/flows/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["edges"] == body["edges"]


def test_list_flows(client: TestClient, sample_flow: dict) -> None:
    assert client.get("/flows").json() == []
    client.post("/flows", json=sample_flow)
    client.post("/flows", json={**sample_flow, "name": "Second"})
    rows = client.get("/flows").json()
    assert [r["name"] for r in rows] == ["Second", "Fix issue"]
    assert rows[0]["node_count"] == 5


def test_update_flow(client: TestClient, sample_flow: dict) -> None:
    flow_id = client.post("/flows", json=sample_flow).json()["id"]
    resp = client.put(f"/flows/{flow_id}", json={"name": "Renamed", "description": "d"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed"
    assert len(resp.json()["nodes"]) == 5  # untouched

    # Replacing nodes without edges must re-validate against existing edges.
    resp = client.put(f"/flows/{flow_id}", json={"nodes": sample_flow["nodes"][:2]})
    assert resp.status_code == 422
    assert "unknown node" in resp.text

    resp = client.put(
        f"/flows/{flow_id}",
        json={"nodes": sample_flow["nodes"][:2], "edges": sample_flow["edges"][:1]},
    )
    assert resp.status_code == 200
    assert len(resp.json()["nodes"]) == 2


def test_delete_flow(client: TestClient, sample_flow: dict) -> None:
    flow_id = client.post("/flows", json=sample_flow).json()["id"]
    assert client.delete(f"/flows/{flow_id}").status_code == 204
    assert client.get(f"/flows/{flow_id}").status_code == 404
    assert client.delete(f"/flows/{flow_id}").status_code == 404


def test_duplicate_flow(client: TestClient, sample_flow: dict) -> None:
    flow_id = client.post("/flows", json=sample_flow).json()["id"]
    first = client.post(f"/flows/{flow_id}/duplicate")
    assert first.status_code == 201
    assert first.json()["name"] == "Fix issue (copy)"
    assert first.json()["id"] != flow_id
    assert first.json()["nodes"] == client.get(f"/flows/{flow_id}").json()["nodes"]
    second = client.post(f"/flows/{flow_id}/duplicate")
    assert second.json()["name"] == "Fix issue (copy 2)"


def test_validation_rejects_bad_graphs(client: TestClient, sample_flow: dict) -> None:
    # duplicate node id
    bad = {**sample_flow, "nodes": sample_flow["nodes"] + [sample_flow["nodes"][0]]}
    assert client.post("/flows", json=bad).status_code == 422

    # dangling edge
    bad = {
        **sample_flow,
        "edges": sample_flow["edges"] + [{"id": "x", "source": "plan", "target": "nope"}],
    }
    assert client.post("/flows", json=bad).status_code == 422

    # bad on_fail
    node = {
        **sample_flow["nodes"][0],
        "data": {**sample_flow["nodes"][0]["data"], "on_fail": "explode"},
    }
    bad = {**sample_flow, "nodes": [node] + sample_flow["nodes"][1:]}
    assert client.post("/flows", json=bad).status_code == 422

    # goto to missing step
    node = {
        **sample_flow["nodes"][0],
        "data": {**sample_flow["nodes"][0]["data"], "on_fail": "goto:ghost"},
    }
    bad = {**sample_flow, "nodes": [node] + sample_flow["nodes"][1:]}
    assert client.post("/flows", json=bad).status_code == 422

    # unknown tool
    node = {
        **sample_flow["nodes"][0],
        "data": {**sample_flow["nodes"][0]["data"], "tools_allowed": ["rm_rf"]},
    }
    bad = {**sample_flow, "nodes": [node] + sample_flow["nodes"][1:]}
    assert client.post("/flows", json=bad).status_code == 422

    # condition edge without true/false handle
    edges = [e if e["id"] != "e4" else {**e, "sourceHandle": None} for e in sample_flow["edges"]]
    bad = {**sample_flow, "edges": edges}
    assert client.post("/flows", json=bad).status_code == 422

    # unknown step type
    node = {
        **sample_flow["nodes"][0],
        "data": {**sample_flow["nodes"][0]["data"], "type": "deploy"},
    }
    bad = {**sample_flow, "nodes": [node] + sample_flow["nodes"][1:]}
    assert client.post("/flows", json=bad).status_code == 422


def test_empty_flow_is_valid(client: TestClient) -> None:
    resp = client.post("/flows", json={"name": "Empty"})
    assert resp.status_code == 201
    assert resp.json()["nodes"] == []
