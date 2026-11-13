"""Knowledge base: chunking, lexical retrieval, guard rail, tone settings, prompt composition."""

from __future__ import annotations

import io

import httpx
import pytest
from docx import Document
from fastapi.testclient import TestClient

from app.kb import guard
from app.kb import service as kb
from app.kb.embed import LexicalEmbedder, OpenAIEmbedder, cosine, tokens
from tests.conftest import login

GYM = {
    "name": "Gym A",
    "industry": "gym",
    "branch_count": 1,
    "main_channel": "zalo",
    "current_software": "",
}
PRICE_TEXT = """Bảng giá Gym A 2026

Gói tập 1 tháng: 500.000đ, tập không giới hạn giờ, tặng 1 buổi PT thử.

Gói tập 3 tháng: 1.200.000đ (tiết kiệm 20%). Đóng băng tối đa 15 ngày.

Gói PT 10 buổi: 3.000.000đ, mỗi buổi 60 phút với huấn luyện viên riêng.

Giờ mở cửa: 5h30 - 22h00 tất cả các ngày trong tuần, kể cả lễ.

Chính sách hoàn tiền: không hoàn tiền sau khi kích hoạt gói, được chuyển nhượng cho người khác một lần."""


def _onboard(client: TestClient, email: str) -> dict:
    login(client, email)
    r = client.post("/onboarding", json=GYM)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------- pure functions


def test_tokens_and_lexical_similarity() -> None:
    assert tokens("Giá gói tập 3 tháng bao nhiêu?") == ["gia", "goi", "tap", "thang"]
    emb = LexicalEmbedder()
    a, b, c = (
        emb._vector("gói tập 3 tháng giá 1.200.000"),
        emb._vector("gói tập ba tháng giá bao nhiêu"),
        emb._vector("giờ mở cửa buổi sáng"),
    )
    assert cosine(a, b) > cosine(a, c) > -1
    assert cosine(a, a) == pytest.approx(1.0)


def test_chunking_keeps_paragraphs_and_overlaps() -> None:
    chunks = kb.chunk_text(PRICE_TEXT, size=160, overlap=30)
    assert len(chunks) >= 3 and all(len(c) <= 200 for c in chunks)
    assert chunks[0].startswith("Bảng giá Gym A 2026")
    assert any("Chính sách hoàn tiền" in c for c in chunks)
    joined = " ".join(chunks)
    for key in ("500.000đ", "1.200.000đ", "3.000.000đ", "5h30 - 22h00"):
        assert key in joined
    long = "a" * 2000
    assert all(len(c) <= 900 for c in kb.chunk_text(long))
    assert kb.chunk_text("   ") == []


def test_guard_rail_categories() -> None:
    assert guard.check("Em bị đau lưng tập được không?").category == "chấn thương"
    assert guard.check("Có nên uống whey trước khi tập?").category == "thuốc / thực phẩm chức năng"
    assert (
        guard.check("Chị đang mang thai 3 tháng, tập yoga được không").category == "y tế / bệnh lý"
    )
    assert guard.check("Cho em xin thực đơn giảm cân 1 tuần").category == "dinh dưỡng chuyên sâu"
    assert not guard.check("Gói 3 tháng giá bao nhiêu, mở cửa mấy giờ?").handoff
    assert not guard.check("Em muốn đặt lịch PT sáng mai").handoff
    assert guard.check("Bên mình có nhận khách VIP không", ["vip"]).matched == "vip"
    assert not guard.check("Em bị đau lưng", enabled=False).handoff
    assert guard.check("Em bị đau lưng", ["abc"], enabled=False).handoff is False


async def test_openai_embedder_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(req.content)
        seen.append(body)
        assert req.headers["authorization"] == "Bearer k"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [float(i), 1.0]} for i in range(len(body["input"]))
                ]
            },
        )

    emb = OpenAIEmbedder("k", transport=httpx.MockTransport(handler))
    vecs = await emb.embed([f"t{i}" for i in range(70)])
    assert len(vecs) == 70 and len(seen) == 2 and vecs[65] == [1.0, 1.0]


# ---------------------------------------------------------------- API


def test_documents_search_and_preview(client: TestClient, anon_client: TestClient) -> None:
    _onboard(client, "kb@example.com")
    # paste text
    r = client.post(
        "/biz/kb/documents/text",
        json={"title": "Bảng giá 2026", "kind": "price", "text": PRICE_TEXT},
    )
    assert r.status_code == 201, r.text
    doc = r.json()
    assert (
        doc["status"] == "ready"
        and doc["chunk_count"] >= 1
        and doc["embedder"] == "lexical"
        and doc["kind_label"] == "Bảng giá"
    )
    # upload a DOCX
    d = Document()
    d.add_paragraph("Nội quy phòng tập")
    d.add_paragraph(
        "Khách mang giày thể thao sạch, lau máy sau khi dùng. Không mang đồ ăn vào khu tập."
    )
    d.add_paragraph("Tủ đồ: miễn phí, không để qua đêm.")
    buf = io.BytesIO()
    d.save(buf)
    r = client.post(
        "/biz/kb/documents",
        params={"kind": "rules", "title": "Nội quy"},
        files={
            "file": (
                "noiquy.docx",
                buf.getvalue(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert r.status_code == 201, r.text
    assert (
        r.json()["chunk_count"] >= 1
        and "giày thể thao" in client.get(f"/biz/kb/documents/{r.json()['id']}/text").json()["text"]
    )
    # unsupported / empty
    assert (
        client.post(
            "/biz/kb/documents", files={"file": ("x.exe", b"MZ", "application/octet-stream")}
        ).status_code
        == 422
    )
    assert (
        client.post("/biz/kb/documents", files={"file": ("x.txt", b"", "text/plain")}).status_code
        == 422
    )

    docs = client.get("/biz/kb/documents").json()
    assert [x["title"] for x in docs] == ["Nội quy", "Bảng giá 2026"]

    hits = client.post(
        "/biz/kb/search", json={"question": "gói 3 tháng giá bao nhiêu?", "k": 2}
    ).json()["hits"]
    assert hits and "1.200.000" in hits[0]["text"] and hits[0]["title"] == "Bảng giá 2026"
    hits = client.post("/biz/kb/search", json={"question": "mấy giờ mở cửa", "k": 1}).json()["hits"]
    assert "5h30" in hits[0]["text"]
    hits = client.post(
        "/biz/kb/search", json={"question": "tủ đồ để qua đêm được không", "k": 1}
    ).json()["hits"]
    assert hits[0]["kind"] == "rules"

    # preview: prompt carries tone, forbidden rules, services and retrieved chunks; guard triggers handoff
    client.put(
        "/biz/ai/settings",
        json={
            "forbidden": ["Không giảm giá dưới mọi hình thức", " "],
            "address_self": "mình",
            "address_customer": "bạn",
            "friendliness": 3,
            "style_examples": ["Oke bạn ơi, mình giữ chỗ 9h sáng mai nhé 💪"],
        },
    )
    p = client.post("/biz/kb/preview", json={"question": "Gói 3 tháng giá nhiêu bạn?"}).json()
    assert p["guard"]["handoff"] is False and p["guard"]["reply"] is None
    sp = p["system_prompt"]
    assert 'xưng "mình" và gọi khách là "bạn"' in sp and "Không giảm giá dưới mọi hình thức" in sp
    assert (
        "Oke bạn ơi" in sp
        and "# Dịch vụ và giá" in sp
        and "PT 10 buổi" in sp
        and "Ghi chú nội bộ" in sp
    )
    assert "# Kiến thức liên quan" in sp and "1.200.000" in sp
    assert sp.index("# Điều cấm") < sp.index("# Dịch vụ và giá") < sp.index("# Kiến thức liên quan")
    p = client.post("/biz/kb/preview", json={"question": "Mình bị đau gối thì tập gì?"}).json()
    assert p["guard"]["handoff"] is True and p["guard"]["category"] == "chấn thương"
    assert (
        p["guard"]["reply"].startswith("Dạ câu này mình xin phép chuyển")
        and "bạn chờ mình" in p["guard"]["reply"]
    )

    # isolation + roles: other workspace sees nothing; staff cannot change settings or upload
    login(client, "staff@example.com")
    login(client, "kb@example.com")
    client.post("/workspaces/current/members", json={"email": "staff@example.com", "role": "staff"})
    login(client, "staff@example.com")
    assert client.get("/biz/kb/documents").status_code == 403  # the assistant setup is owner-only
    assert client.put("/biz/ai/settings", json={"friendliness": 1}).status_code == 403
    assert (
        client.post("/biz/kb/documents/text", json={"title": "x", "text": "y"}).status_code == 403
    )
    _onboard(anon_client, "other@example.com")
    assert anon_client.get("/biz/kb/documents").json() == []
    assert anon_client.post("/biz/kb/search", json={"question": "gói 3 tháng"}).json()["hits"] == []
    assert anon_client.delete(f"/biz/kb/documents/{doc['id']}").status_code == 404
    assert anon_client.get("/biz/ai/settings").json()["address_self"] == "em"

    # delete removes chunks from retrieval
    login(client, "kb@example.com")
    assert client.delete(f"/biz/kb/documents/{doc['id']}").status_code == 204
    assert all(
        h["kind"] != "price"
        for h in client.post("/biz/kb/search", json={"question": "gói 3 tháng giá"}).json()["hits"]
    )


def test_ai_settings_validation(client: TestClient) -> None:
    _onboard(client, "tone@example.com")
    s = client.get("/biz/ai/settings").json()
    assert (
        s["friendliness"] == 2
        and s["medical_guard"] is True
        and "chấn thương" in s["guard_categories"]
    )
    assert (
        s["handoff_preview"]
        == "Dạ câu này em xin phép chuyển cho nhân viên chuyên môn để tư vấn kỹ hơn ạ. anh/chị chờ em một chút nhé!"
    )
    assert client.put("/biz/ai/settings", json={"friendliness": 7}).status_code == 422
    r = client.put(
        "/biz/ai/settings",
        json={
            "medical_guard": False,
            "handoff_keywords": ["bảo hiểm"],
            "handoff_message": "{customer} đợi {self} xíu nha",
        },
    )
    assert r.json()["handoff_preview"] == "anh/chị đợi em xíu nha"
    p = client.post("/biz/kb/preview", json={"question": "em bị đau lưng"}).json()
    assert p["guard"]["handoff"] is False  # medical guard off
    p = client.post("/biz/kb/preview", json={"question": "bên mình có nhận bảo hiểm không"}).json()
    assert p["guard"]["handoff"] is True and p["guard"]["matched"] == "bảo hiểm"
