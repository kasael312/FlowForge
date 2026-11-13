"""Skills: SKILL.md parsing, zip import/export, API, executor prompt + /skills mount."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.executor.engine import ExecutorDeps, FlowExecutor
from app.executor.prompts import system_prompt_for
from app.models import RunStatus, Skill
from app.schemas import StepType
from app.services import skills as svc
from app.skills_builtin import (
    BUILTIN_PERSONAS,
    BUILTIN_SKILLS,
    BUILTIN_SKILLS_EXTRA,
    seed_builtin_skills,
)
from tests.conftest import login, make_step
from tests.fakes import FakeAnthropic, FakeSandbox, text_response
from tests.test_nodes import _edge, _run, _steps

# ---------------------------------------------------------------- pure functions


def test_parse_and_render_skill_md_round_trip() -> None:
    text = svc.render_skill_md(
        "my-skill", "Does things", ["read_file", "run_shell"], "# Body\n\nhello"
    )
    meta, body = svc.parse_skill_md(text)
    assert meta == {
        "name": "my-skill",
        "description": "Does things",
        "tools_required": ["read_file", "run_shell"],
    }
    assert body == "# Body\n\nhello\n"
    # list form and missing front matter
    meta, body = svc.parse_skill_md("---\nname: x\ntools_required:\n  - a\n  - b\n---\nbody")
    assert meta["tools_required"] == ["a", "b"] and body == "body\n"
    assert svc.parse_skill_md("just text") == ({}, "just text")


def test_name_and_path_validation() -> None:
    assert svc.validate_name("  Python-Testing ") == "python-testing"
    for bad in ("x", "has space", "UPPER!", "-lead", "a" * 70):
        with pytest.raises(svc.SkillError):
            svc.validate_name(bad)
    assert svc.safe_rel_path("docs/ref.md") == "docs/ref.md"
    for bad in ("../x", "SKILL.md", "a/../b", "", "."):
        with pytest.raises(svc.SkillError):
            svc.safe_rel_path(bad)


def test_zip_import_export_round_trip(tmp_path: Path) -> None:
    s = Skill(
        id="skill1",
        name="demo",
        description="d",
        content="# Demo\n",
        tools_required=["read_file"],
        files=[],
    )
    svc.put_file(s, "ref/notes.md", b"notes", "text/markdown")
    svc.put_file(s, "script.py", b"print(1)", "text/x-python")
    blob = svc.export_zip(s)
    names = sorted(zipfile.ZipFile(io.BytesIO(blob)).namelist())
    assert names == ["demo/SKILL.md", "demo/ref/notes.md", "demo/script.py"]

    imported = svc.import_zip(blob)
    assert imported.name == "demo" and imported.tools_required == ["read_file"]
    assert imported.content == "# Demo\n" and sorted(p for p, _ in imported.files) == [
        "ref/notes.md",
        "script.py",
    ]

    # SKILL.md at the root, name from the file name
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SKILL.md", "no front matter here")
        zf.writestr("extra.txt", "x")
    imported = svc.import_zip(buf.getvalue(), fallback_name="My Zip")
    assert imported.name == "my-zip" and imported.content == "no front matter here"
    with pytest.raises(svc.SkillError):
        svc.import_zip(b"not a zip")
    with pytest.raises(svc.SkillError):
        buf2 = io.BytesIO()
        with zipfile.ZipFile(buf2, "w") as zf:
            zf.writestr("README.md", "x")
        svc.import_zip(buf2.getvalue())

    root = svc.materialize(s, tmp_path)
    assert (root / "SKILL.md").read_text().startswith("---\nname: demo")
    assert (root / "ref/notes.md").read_bytes() == b"notes"
    svc.remove_file(s, "ref/notes.md")
    root = svc.materialize(s, tmp_path)
    assert not (root / "ref/notes.md").exists()


def test_file_limits() -> None:
    s = Skill(name="demo", description="", content="", tools_required=[], files=[])
    with pytest.raises(svc.SkillError):
        svc.put_file(s, "big.bin", b"x" * (svc.MAX_FILE_BYTES + 1))
    svc.put_file(s, "a.txt", b"1")
    svc.put_file(s, "a.txt", b"22")  # replace
    assert [(f["path"], f["size"]) for f in s.files] == [("a.txt", 2)]
    assert svc.file_bytes(s, "a.txt")[1] == b"22"


def test_prompt_composition_order() -> None:
    out = system_prompt_for(StepType.custom, "STEP", skills="SKILLS", rules="RULES")
    assert out.index("RULES") < out.index("SKILLS") < out.index("STEP")
    assert out.startswith("You are an autonomous")
    assert system_prompt_for(StepType.custom, "STEP").endswith("STEP")


# ---------------------------------------------------------------- API


def _zip(name: str, body: str, files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{name}/SKILL.md", body)
        for p, t in files.items():
            zf.writestr(f"{name}/{p}", t)
    return buf.getvalue()


def test_skills_api(client: TestClient, db_session: Session) -> None:
    all_specs = [*BUILTIN_SKILLS, *BUILTIN_SKILLS_EXTRA, *BUILTIN_PERSONAS]
    assert seed_builtin_skills(db_session) == len(all_specs)
    assert seed_builtin_skills(db_session) == 0
    rows = client.get("/skills").json()
    assert [r["name"] for r in rows] == sorted(s["name"] for s in all_specs)
    assert {r["name"] for r in rows if r["kind"] == "persona"} == {
        p["name"] for p in BUILTIN_PERSONAS
    }
    assert all(r["builtin"] for r in rows)
    pt = next(r for r in rows if r["name"] == "python-testing")
    assert pt["files"][0]["path"] == "patterns.md" and "data" not in pt["files"][0]
    assert pt["skill_md"].startswith("---\nname: python-testing")

    # built-ins are read-only
    assert client.put(f"/skills/{pt['id']}", json={"content": "x"}).status_code == 403
    assert client.delete(f"/skills/{pt['id']}").status_code == 403

    # create + validation + uniqueness
    r = client.post("/skills", json={"name": "My Skill", "content": "# hi"})
    assert r.status_code == 422
    r = client.post(
        "/skills",
        json={
            "name": "my-skill",
            "description": "d",
            "content": "# hi",
            "tools_required": ["read_file"],
        },
    )
    assert r.status_code == 201, r.text
    mine = r.json()
    assert client.post("/skills", json={"name": "my-skill"}).status_code == 409
    rows = client.get("/skills").json()
    assert rows[-1]["name"] == "my-skill" and not rows[-1]["builtin"]

    # files: upload, download, delete
    r = client.post(
        f"/skills/{mine['id']}/files",
        files={"file": ("ref.md", b"# ref", "text/markdown")},
        params={"path": "docs/ref.md"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["files"] == [
        {"path": "docs/ref.md", "size": 5, "content_type": "text/markdown"}
    ]
    r = client.get(f"/skills/{mine['id']}/files/docs/ref.md")
    assert r.status_code == 200 and r.content == b"# ref"
    assert (
        client.post(
            f"/skills/{mine['id']}/files", files={"file": ("x", b"x")}, params={"path": "../x"}
        ).status_code
        == 422
    )
    assert client.delete(f"/skills/{mine['id']}/files/docs/ref.md").json()["files"] == []
    assert client.delete(f"/skills/{mine['id']}/files/docs/ref.md").status_code == 404

    # update
    r = client.put(f"/skills/{mine['id']}", json={"content": "# changed", "tools_required": []})
    assert r.json()["content"] == "# changed"

    # duplicate a built-in → editable copy with files
    r = client.post(f"/skills/{pt['id']}/duplicate")
    assert r.status_code == 201
    copy = r.json()
    assert (
        copy["name"] == "python-testing"
        and not copy["builtin"]
        and copy["files"][0]["path"] == "patterns.md"
    )
    assert client.post(f"/skills/{pt['id']}/duplicate").json()["name"] == "python-testing-2"

    # export / import
    r = client.get(f"/skills/{copy['id']}/export")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert "python-testing/SKILL.md" in names and "python-testing/patterns.md" in names
    blob = _zip(
        "imported-one",
        "---\nname: imported-one\ndescription: via zip\ntools_required: [web_search]\n---\n# Imported\n",
        {"a/b.txt": "hey"},
    )
    r = client.post("/skills/import", files={"file": ("imported-one.zip", blob, "application/zip")})
    assert r.status_code == 201, r.text
    imp = r.json()
    assert (
        imp["description"] == "via zip"
        and imp["tools_required"] == ["web_search"]
        and imp["files"][0]["path"] == "a/b.txt"
    )
    assert (
        client.post(
            "/skills/import", files={"file": ("bad.zip", b"nope", "application/zip")}
        ).status_code
        == 422
    )

    # isolation: another user cannot see or touch my skills, but sees built-ins
    login(client, "other@example.com")
    other_rows = client.get("/skills").json()
    assert all(r["builtin"] for r in other_rows)
    assert client.get(f"/skills/{mine['id']}").status_code == 404
    assert client.delete(f"/skills/{mine['id']}").status_code == 404


# ---------------------------------------------------------------- executor


async def test_executor_mounts_skills_and_extends_prompt(
    deps: ExecutorDeps, db_session: Session, fake_sandbox: FakeSandbox, fake_client: FakeAnthropic
) -> None:
    s = Skill(
        user_id=None,
        name="python-testing",
        description="How to test",
        content="# Testing rules\nUse pytest.",
        tools_required=["run_shell"],
        files=[],
    )
    svc.put_file(s, "patterns.md", b"fixtures...", "text/markdown")
    db_session.add(s)
    db_session.commit()
    deps.skill_loader = lambda uid, ids: svc.load_bundles(db_session, uid, ids)
    fake_client.responses = [text_response("done"), text_response("done2")]

    nodes = [
        make_step("a", "custom", system_prompt="You are step A.", skill_ids=[s.id, "missing-id"]),
        make_step("b", "custom", system_prompt="You are step B."),
    ]
    run_id = _run(db_session, nodes, [_edge("a", "b")])
    assert await FlowExecutor(deps, run_id).execute() is RunStatus.success

    assert fake_sandbox.binaries["/skills/python-testing/SKILL.md"].startswith(b"# python-testing")
    assert fake_sandbox.binaries["/skills/python-testing/patterns.md"] == b"fixtures..."
    sys_a = fake_client.requests[0]["system"]
    assert "## Skill: python-testing" in sys_a and "Use pytest." in sys_a
    assert "/skills/python-testing/patterns.md" in sys_a
    assert sys_a.index("Use pytest.") < sys_a.index("You are step A.")
    assert "Skill:" not in fake_client.requests[1]["system"]  # step b has no skills
    msgs = [e["message"] for e in deps.bus.history(run_id) if e["event"] == "log"]
    assert any("skill missing-id not found" in m for m in msgs)
    assert any(m.startswith("skills mounted: python-testing (1 files)") for m in msgs)
    assert [st.status.value for st in _steps(deps, run_id)] == ["success", "success"]


def test_try_step_includes_skills(
    client: TestClient, db_session: Session, sample_flow: dict
) -> None:
    r = client.post(
        "/skills", json={"name": "tone", "description": "Tone guide", "content": "Be brief."}
    )
    sid = r.json()["id"]
    flow = client.post("/flows", json=sample_flow).json()
    node = make_step("x", "custom", system_prompt="Write a reply.", skill_ids=[sid])
    r = client.post(f"/flows/{flow['id']}/steps/x/try", json={"node": node, "task": "t"})
    assert r.status_code == 200, r.text
    assert (
        "## Skill: tone" in r.json()["system_prompt"] and "Be brief." in r.json()["system_prompt"]
    )


def test_persona_kind_and_prompt_order(client: TestClient) -> None:
    p = client.post(
        "/skills",
        json={
            "name": "writer",
            "kind": "persona",
            "description": "You write",
            "content": "Be warm.",
        },
    )
    assert p.status_code == 201 and p.json()["kind"] == "persona"
    assert "kind: persona" in p.json()["skill_md"]
    k = client.post("/skills", json={"name": "format", "content": "Use bullets."}).json()
    assert k["kind"] == "skill" and "kind:" not in k["skill_md"]
    bundles = [
        svc.SkillBundle(
            id="1",
            name="format",
            description="",
            tools_required=[],
            content="Use bullets.",
            files=[],
        ),
        svc.SkillBundle(
            id="2",
            name="writer",
            description="You write",
            tools_required=[],
            content="Be warm.",
            files=[],
            kind="persona",
        ),
    ]
    block = svc.skills_prompt_block(bundles)
    assert block.index("## Persona: writer") < block.index("## Skill: format")
    assert svc.import_zip(client.get(f"/skills/{p.json()['id']}/export").content).kind == "persona"
    assert client.put(f"/skills/{k['id']}", json={"kind": "persona"}).json()["kind"] == "persona"
