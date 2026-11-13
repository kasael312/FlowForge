"""File tools: read PDF/DOCX/XLSX/CSV, write DOCX/XLSX/PDF, OCR images.

Documents live in the run's sandbox; bytes are moved in and out with
``Sandbox.read_bytes`` / ``Sandbox.write_bytes`` and parsed on the backend.
"""

from __future__ import annotations

import csv
import io
import json
import posixpath
from typing import Any

from app.executor.sandbox import REPO_ROOT
from app.tools.base import Tool, ToolCategory, ToolContext, ToolError, schema, truncate
from app.tools.code import safe_repo_path

MAX_DOC_BYTES = 25_000_000


def _ext(path: str) -> str:
    return posixpath.splitext(path)[1].lower().lstrip(".")


def extract_text(data: bytes, ext: str, *, max_rows: int = 200) -> str:
    """Text content of a document by extension."""
    if ext == "pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = [(p.extract_text() or "") for p in reader.pages]
        return "\n\n".join(f"--- page {i + 1} ---\n{t}" for i, t in enumerate(pages))
    if ext == "docx":
        import docx

        d = docx.Document(io.BytesIO(data))
        parts = [p.text for p in d.paragraphs if p.text.strip()]
        for t in d.tables:
            for row in t.rows:
                parts.append(" | ".join(c.text.strip() for c in row.cells))
        return "\n".join(parts)
    if ext in ("xlsx", "xlsm"):
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        out = []
        for ws in wb.worksheets:
            out.append(f"## sheet {ws.title}")
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i >= max_rows:
                    out.append(f"... ({ws.max_row - max_rows} more rows)")
                    break
                out.append(",".join("" if v is None else str(v) for v in row))
        return "\n".join(out)
    if ext in ("csv", "tsv"):
        text = data.decode("utf-8", errors="replace")
        rows = list(csv.reader(io.StringIO(text), delimiter="\t" if ext == "tsv" else ","))
        head = rows[:max_rows]
        body = "\n".join(",".join(r) for r in head)
        return body + (f"\n... ({len(rows) - max_rows} more rows)" if len(rows) > max_rows else "")
    if ext in ("txt", "md", "json", "html", "xml", "yaml", "yml"):
        return data.decode("utf-8", errors="replace")
    raise ToolError(f"unsupported document type .{ext}")


def build_document(ext: str, content: str | list[list[Any]], *, title: str = "") -> bytes:
    """Bytes of a new DOCX / XLSX / PDF document."""
    if ext == "docx":
        import docx

        d = docx.Document()
        if title:
            d.add_heading(title, level=1)
        for para in str(content).split("\n"):
            if para.startswith("# "):
                d.add_heading(para[2:], level=1)
            elif para.startswith("## "):
                d.add_heading(para[3:], level=2)
            elif para.startswith("- "):
                d.add_paragraph(para[2:], style="List Bullet")
            else:
                d.add_paragraph(para)
        buf = io.BytesIO()
        d.save(buf)
        return buf.getvalue()
    if ext == "xlsx":
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = title or "Sheet1"
        rows = content if isinstance(content, list) else list(csv.reader(io.StringIO(str(content))))
        for row in rows:
            ws.append(list(row))
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()
    if ext == "pdf":
        from fpdf import FPDF
        from fpdf.enums import XPos, YPos

        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        pdf.set_font("Helvetica", size=11)
        nxt = {"new_x": XPos.LMARGIN, "new_y": YPos.NEXT}
        if title:
            pdf.set_font("Helvetica", style="B", size=16)
            pdf.multi_cell(0, 10, title, **nxt)
            pdf.set_font("Helvetica", size=11)
        for para in str(content).split("\n"):
            line = para.encode("latin-1", errors="replace").decode("latin-1")
            if line.strip():
                pdf.multi_cell(0, 6, line, **nxt)
            else:
                pdf.ln(6)
        return bytes(pdf.output())
    raise ToolError(f"unsupported output type .{ext}")


class ReadDocument(Tool):
    name = "read_document"
    description = (
        "Read the text of a PDF, DOCX, XLSX, CSV/TSV or plain-text file from the repository."
    )
    category = ToolCategory.files
    needs_sandbox = True
    input_schema = schema(
        {
            "path": {"type": "string"},
            "max_chars": {"type": "integer", "description": "Default 20000"},
        },
        ["path"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        path = safe_repo_path(args["path"])
        data = await ctx.sandbox_required().read_bytes(path)
        if len(data) > MAX_DOC_BYTES:
            raise ToolError("document is too large")
        text = extract_text(data, _ext(path))
        return truncate(text, max(500, min(int(args.get("max_chars") or 20_000), 200_000)))


class WriteDocument(Tool):
    name = "write_document"
    description = (
        "Create a DOCX, XLSX or PDF file in the repository. For DOCX/PDF pass text (lines starting "
        "with '# ' / '## ' become headings, '- ' bullets). For XLSX pass rows as a JSON array of arrays or CSV text."
    )
    category = ToolCategory.files
    needs_sandbox = True
    input_schema = schema(
        {
            "path": {
                "type": "string",
                "description": "Relative path ending in .docx, .xlsx or .pdf",
            },
            "content": {"type": "string"},
            "title": {"type": "string"},
        },
        ["path", "content"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        path = safe_repo_path(args["path"])
        ext = _ext(path)
        content: str | list[list[Any]] = args["content"]
        if ext == "xlsx" and isinstance(content, str) and content.lstrip().startswith("["):
            try:
                content = json.loads(content)
            except json.JSONDecodeError as exc:
                raise ToolError(f"content is not valid JSON rows: {exc}") from exc
        data = build_document(ext, content, title=args.get("title") or "")
        await ctx.sandbox_required().write_bytes(path, data)
        return f"wrote {len(data)} bytes to {posixpath.relpath(path, REPO_ROOT)}"


class OcrImage(Tool):
    name = "ocr_image"
    description = "Extract text from an image (PNG/JPG) with Tesseract OCR running in the sandbox."
    category = ToolCategory.files
    needs_sandbox = True
    input_schema = schema(
        {
            "path": {"type": "string"},
            "lang": {"type": "string", "description": "Tesseract language, default eng"},
        },
        ["path"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        path = safe_repo_path(args["path"])
        lang = (args.get("lang") or "eng").strip()
        if not lang.replace("+", "").isalnum():
            raise ToolError("invalid lang")
        res = await ctx.sandbox_required().exec(["tesseract", path, "-", "-l", lang], timeout=300)
        if res.exit_code == 127:
            raise ToolError("tesseract is not installed in the sandbox image")
        if not res.ok:
            raise ToolError(res.stderr.strip() or "ocr failed")
        return truncate(res.stdout)


FILE_TOOLS: list[Tool] = [ReadDocument(), WriteDocument(), OcrImage()]
