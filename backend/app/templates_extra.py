"""Built-in templates for general AI workflows (research, content, email, documents, data, meetings).

Each template declares its run inputs and relies on the tool plugins, the
non-LLM node types and (for Inbox triage) the ``approval`` step. They are
seeded next to the coding templates in :mod:`app.templates`.
"""

from __future__ import annotations

from typing import Any

COL, ROW = 260, 150


def node(id_: str, type_: str, x: int, y: int = 0, **data: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "label": id_,
        "type": type_,
        "system_prompt": "",
        "tools_allowed": [],
        "max_turns": 15,
        "on_fail": "stop",
        "retry_limit": 1,
        "command": None,
        "expression": None,
    }
    base.update(data)
    return {"id": id_, "type": "step", "position": {"x": x, "y": y}, "data": base}


def edge(src: str, dst: str, handle: str | None = None) -> dict[str, Any]:
    return {
        "id": f"{src}->{dst}{':' + handle if handle else ''}",
        "source": src,
        "target": dst,
        "sourceHandle": handle,
    }


def field(name: str, type_: str = "text", **extra: Any) -> dict[str, Any]:
    f: dict[str, Any] = {
        "name": name,
        "label": extra.pop("label", name.replace("_", " ").title()),
        "type": type_,
        "required": False,
        "default": None,
        "options": [],
        "placeholder": "",
        "description": "",
    }
    f.update(extra)
    return f


EXTRA_TEMPLATES: list[dict[str, Any]] = [
    # ------------------------------------------------------------------ 1
    {
        "key": "research-report",
        "name": "Research report",
        "skills": {
            "search": ["researcher"],
            "read": ["researcher"],
            "synth": ["researcher", "citation-format"],
        },
        "rules": [
            {
                "name": "Cite or drop",
                "content": "Every factual claim in the report must carry a [n] citation to one of the sources read; drop claims you cannot source.",
                "scope": "flow",
            }
        ],
        "description": "Topic → web search → read the best sources → synthesis with citations → DOCX report.",
        "input_schema": [
            field("topic", required=True, placeholder="e.g. battery recycling in Southeast Asia"),
            field("sources", "number", label="Sources to read", default=5),
            field("language", "select", options=["English", "Vietnamese"], default="English"),
        ],
        "sandbox": {"network": "none"},
        "nodes": [
            node(
                "search",
                "custom",
                0,
                label="Search the web",
                tools_allowed=["web_search"],
                max_turns=6,
                system_prompt="Research topic: {{input.topic}}.\nRun 2-3 web searches with different phrasings. Return a numbered list of the {{input.sources}} most relevant, reputable URLs with a one-line reason each. Output only the list.",
            ),
            node(
                "read",
                "custom",
                COL,
                label="Read sources",
                tools_allowed=["scrape", "fetch_url"],
                max_turns=12,
                system_prompt="Open each URL from the previous step with the scrape tool (fall back to fetch_url). For every source write: title, URL, 3-6 key facts with numbers/dates, and notable quotes. Keep the source numbering. Output as structured notes.",
            ),
            node(
                "synth",
                "custom",
                COL * 2,
                label="Synthesise with citations",
                max_turns=4,
                system_prompt="Write a research report in {{input.language}} about {{input.topic}} using ONLY the notes below. Structure: Executive summary, Background, Key findings (each with [n] citations), Risks/open questions, Conclusion, References (numbered list of URLs). Use Markdown with '# ' and '## ' headings and '- ' bullets.\n\nNotes:\n{{prev_output}}",
            ),
            node(
                "docx",
                "custom",
                COL * 3,
                label="Export DOCX",
                tools_allowed=["write_document"],
                max_turns=3,
                system_prompt="Save the report below as output/report.docx using the write_document tool (title: '{{input.topic}}'). Then reply with the single line DONE.\n\n{{steps.synth.output}}",
            ),
            node(
                "result",
                "output",
                COL * 4,
                label="Report file",
                output_kind="file",
                output_source="output/report.docx",
            ),
        ],
        "edges": [
            edge("search", "read"),
            edge("read", "synth"),
            edge("synth", "docx"),
            edge("docx", "result"),
        ],
    },
    # ------------------------------------------------------------------ 2
    {
        "key": "blog-pipeline",
        "name": "Blog pipeline",
        "skills": {
            "outline": ["copywriter"],
            "draft": ["copywriter", "brand-voice-template"],
            "review": ["brand-voice-template"],
        },
        "description": "Brief → outline → draft → brand-voice review loop → hero image → publish to Notion.",
        "input_schema": [
            field(
                "brief",
                "textarea",
                required=True,
                placeholder="What the post is about, audience, key points",
            ),
            field(
                "tone",
                "select",
                options=["friendly", "professional", "playful"],
                default="friendly",
            ),
            field(
                "notion_database_id",
                label="Notion database ID",
                placeholder="leave empty to skip publishing",
            ),
        ],
        "sandbox": {"network": "none"},
        "nodes": [
            node(
                "outline",
                "custom",
                0,
                label="Outline",
                max_turns=3,
                system_prompt="You are a content strategist. From this brief write a blog outline: working title, hook, 4-6 H2 sections with 2-3 bullet points each, and a CTA. Tone: {{input.tone}}.\n\nBrief: {{input.brief}}",
            ),
            node(
                "draft",
                "custom",
                COL,
                label="Draft",
                max_turns=3,
                on_fail="retry",
                system_prompt="Write the full blog post (800-1200 words, Markdown) from the outline. Tone: {{input.tone}}. If reviewer feedback exists below, apply every point.\n\nOutline:\n{{steps.outline.output}}\n\nReviewer feedback (may be empty):\n{{steps.review.output}}",
            ),
            node(
                "review",
                "custom",
                COL * 2,
                label="Brand-voice reviewer",
                max_turns=2,
                system_prompt="You are the brand-voice reviewer. Check the draft for tone ({{input.tone}}), clarity, structure and factual caution. If it is ready reply exactly 'APPROVED' on the first line followed by one sentence. Otherwise reply 'REVISE' on the first line and a numbered list of concrete edits.\n\nDraft:\n{{steps.draft.output}}",
            ),
            node(
                "gate",
                "condition",
                COL * 3,
                label="Approved?",
                expression='startswith("APPROVED")',
            ),
            node(
                "image",
                "custom",
                COL * 4,
                label="Hero image",
                tools_allowed=["generate_image"],
                max_turns=3,
                system_prompt="Create one hero image for this post with generate_image (path output/hero.png). Describe the scene in one vivid prompt matching the post's topic; avoid text in the image. Then reply with the prompt you used.\n\nPost title and intro:\n{{steps.draft.output}}",
            ),
            node(
                "publish",
                "custom",
                COL * 5,
                label="Publish to Notion",
                tools_allowed=["notion"],
                max_turns=3,
                system_prompt="If the Notion database id '{{input.notion_database_id}}' is empty, reply SKIPPED. Otherwise create a page in that database with the post title as title and the post body as text using the notion tool, then reply with the page URL.\n\nPost:\n{{steps.draft.output}}",
            ),
            node(
                "result",
                "output",
                COL * 6,
                label="Post",
                output_kind="text",
                output_source="{{steps.draft.output}}",
            ),
        ],
        "edges": [
            edge("outline", "draft"),
            edge("draft", "review"),
            edge("review", "gate"),
            edge("gate", "image", "true"),
            edge("gate", "draft", "false"),
            edge("image", "publish"),
            edge("publish", "result"),
        ],
    },
    # ------------------------------------------------------------------ 3
    {
        "key": "inbox-triage",
        "name": "Inbox triage",
        "skills": {"classify": ["support-agent"], "draft": ["support-agent", "email-tone-guide"]},
        "rules": [
            {
                "name": "No promises",
                "content": "Never promise refunds, delivery dates or features you cannot verify from the conversation; say what you will check instead.",
                "scope": "flow",
            }
        ],
        "description": "Incoming email (IMAP trigger) → classify → draft a reply → human approval → send.",
        "input_schema": [
            field("email_from", label="From (manual runs)", placeholder="customer@example.com"),
            field("email_subject", label="Subject (manual runs)"),
            field("email_body", "textarea", label="Body (manual runs)"),
        ],
        "sandbox": {"network": "none"},
        "nodes": [
            node(
                "classify",
                "custom",
                0,
                label="Classify",
                max_turns=2,
                system_prompt='Classify this email. Reply with JSON only: {"category": one of ["support","sales","billing","spam","other"], "priority": "low|normal|high", "summary": "one sentence", "needs_reply": true|false}.\n\nEmail (from trigger): {{input.email}}\nManual fields: from={{input.email_from}} subject={{input.email_subject}}\n{{input.email_body}}',
            ),
            node(
                "needs_reply",
                "condition",
                COL,
                label="Needs reply?",
                expression='contains("\\"needs_reply\\": true")',
            ),
            node(
                "draft",
                "custom",
                COL * 2,
                label="Draft reply",
                max_turns=2,
                system_prompt="Write a concise, polite reply email (plain text, no subject line) to this message. Category/priority: {{steps.classify.output}}.\n\nOriginal email: {{input.email}} {{input.email_body}}",
            ),
            node(
                "review",
                "approval",
                COL * 3,
                label="Human approval",
                approval_timeout_seconds=86400,
                system_prompt="Send this reply to {{input.email.from}}{{input.email_from}}?\n\nSubject: Re: {{input.email.subject}}{{input.email_subject}}\n\n{{steps.draft.output}}",
            ),
            node(
                "send",
                "output",
                COL * 4,
                label="Send reply",
                output_kind="text",
                output_source="{{steps.draft.output}}",
                deliver_tool="send_email",
                deliver_args={
                    "to": "{{input.email.from}}{{input.email_from}}",
                    "subject": "Re: {{input.email.subject}}{{input.email_subject}}",
                    "body": "{{output}}",
                },
            ),
            node(
                "archive",
                "output",
                COL * 2,
                ROW,
                label="No reply needed",
                output_kind="text",
                output_source="{{steps.classify.output}}",
            ),
        ],
        "edges": [
            edge("classify", "needs_reply"),
            edge("needs_reply", "draft", "true"),
            edge("needs_reply", "archive", "false"),
            edge("draft", "review"),
            edge("review", "send"),
        ],
    },
    # ------------------------------------------------------------------ 4
    {
        "key": "invoice-extractor",
        "name": "Invoice extractor",
        "skills": {"extract": ["document-extractor"]},
        "rules": [
            {
                "name": "Never guess amounts",
                "content": "Amounts, dates and identifiers come only from the document text; use null when unreadable.",
                "scope": "step",
                "step_id": "extract",
            }
        ],
        "description": "Upload a PDF/image → OCR → extract fields as JSON → validate → append to Google Sheets → Slack notice.",
        "input_schema": [
            field("invoice", "file", required=True, description="PDF or image of the invoice"),
            field("spreadsheet_id", label="Google Sheet ID", placeholder="1AbC…"),
            field("sheet_range", label="Sheet range", default="Invoices!A:F"),
        ],
        "sandbox": {"network": "none"},
        "nodes": [
            node(
                "ocr",
                "custom",
                0,
                label="OCR / read",
                tools_allowed=["read_document", "ocr_image", "list_files"],
                max_turns=4,
                system_prompt="The invoice is at {{input.invoice.path}}. If it is a PDF use read_document; if it is an image use ocr_image. Return the raw text.",
            ),
            node(
                "extract",
                "custom",
                COL,
                label="Extract fields",
                max_turns=2,
                system_prompt='From the invoice text return JSON only: {"vendor": str, "invoice_number": str, "date": "YYYY-MM-DD", "currency": str, "subtotal": number, "tax": number, "total": number, "line_items": [{"description": str, "qty": number, "unit_price": number}]}. Use null when unknown.\n\n{{prev_output}}',
            ),
            node(
                "validate",
                "data_transform",
                COL * 2,
                label="Validate",
                language="python",
                code="import json\nd = steps['extract']['data'] or json.loads(steps['extract']['output'])\nerrors = [k for k in ('vendor', 'invoice_number', 'date', 'total') if not d.get(k)]\nif d.get('subtotal') is not None and d.get('tax') is not None and d.get('total') is not None and abs(d['subtotal'] + d['tax'] - d['total']) > 0.05:\n    errors.append('subtotal + tax != total')\nemit({'ok': not errors, 'errors': errors, 'row': [d.get('date'), d.get('vendor'), d.get('invoice_number'), d.get('currency'), d.get('subtotal'), d.get('tax'), d.get('total')]})",
            ),
            node(
                "valid",
                "condition",
                COL * 3,
                label="Valid?",
                expression='contains("\\"ok\\": true")',
            ),
            node(
                "sheets",
                "custom",
                COL * 4,
                label="Append to Sheets",
                tools_allowed=["google_sheets"],
                max_turns=3,
                system_prompt="Append this row to spreadsheet '{{input.spreadsheet_id}}' range '{{input.sheet_range}}' with google_sheets (action append). If the spreadsheet id is empty reply SKIPPED.\nRow: {{steps.validate.data.row}}",
            ),
            node(
                "notify",
                "output",
                COL * 5,
                label="Notify",
                output_kind="json",
                output_source="{{steps.extract.output}}",
                deliver_tool="slack_post",
                deliver_args={
                    "text": "Invoice processed: {{steps.extract.data.vendor}} #{{steps.extract.data.invoice_number}} total {{steps.extract.data.total}} {{steps.extract.data.currency}}"
                },
            ),
            node(
                "invalid",
                "output",
                COL * 4,
                ROW,
                label="Validation failed",
                output_kind="json",
                output_source="{{steps.validate.output}}",
            ),
        ],
        "edges": [
            edge("ocr", "extract"),
            edge("extract", "validate"),
            edge("validate", "valid"),
            edge("valid", "sheets", "true"),
            edge("valid", "invalid", "false"),
            edge("sheets", "notify"),
        ],
    },
    # ------------------------------------------------------------------ 5
    {
        "key": "weekly-data-report",
        "name": "Weekly data report",
        "skills": {"query": ["data-analyst", "data-analysis-checklist"]},
        "description": "Schedule → SQL query → Python chart → HTML template → email. Add a schedule trigger after creating the flow.",
        "input_schema": [
            field(
                "sql",
                "textarea",
                label="SQL (read-only)",
                required=True,
                default="SELECT date, revenue FROM daily_sales ORDER BY date DESC LIMIT 7",
            ),
            field("recipients", required=True, placeholder="ceo@example.com, ops@example.com"),
            field("title", default="Weekly report"),
        ],
        "sandbox": {"network": "none"},
        "nodes": [
            node(
                "query",
                "custom",
                0,
                label="Run SQL",
                tools_allowed=["query_sql"],
                max_turns=2,
                system_prompt="Run exactly this query with query_sql and return the CSV result unchanged:\n{{input.sql}}",
            ),
            node(
                "chart",
                "data_transform",
                COL,
                label="Chart + stats",
                language="python",
                code="import io, pandas as pd, matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\ncsv = steps['query']['output']\ncsv = csv[csv.find('\\n') + 1:] if csv.startswith('exit_code') else csv\ndf = pd.read_csv(io.StringIO(csv))\nnum = df.select_dtypes('number')\nax = df.plot(x=df.columns[0], y=list(num.columns)[:3], kind='line', marker='o', title='{{input.title}}')\nax.get_figure().savefig('/repo/output/chart.png', bbox_inches='tight')\nemit({'rows': len(df), 'columns': list(df.columns), 'summary': {c: {'min': float(num[c].min()), 'max': float(num[c].max()), 'mean': round(float(num[c].mean()), 2)} for c in num.columns}, 'table': df.head(20).to_dict('records')})",
            ),
            node(
                "html",
                "template",
                COL * 2,
                label="HTML email",
                output_path="output/report.html",
                template='<h1>{{ input.title }}</h1>\n<p>{{ steps.chart.data.rows }} rows. Generated by FlowForge.</p>\n<table border="1" cellpadding="4">\n<tr>{% for c in steps.chart.data.columns %}<th>{{ c }}</th>{% endfor %}</tr>\n{% for r in steps.chart.data.table %}<tr>{% for c in steps.chart.data.columns %}<td>{{ r[c] }}</td>{% endfor %}</tr>\n{% endfor %}</table>\n<h2>Summary</h2>\n<ul>{% for k, v in steps.chart.data.summary.items() %}<li>{{ k }}: min {{ v.min }}, max {{ v.max }}, mean {{ v.mean }}</li>{% endfor %}</ul>\n<p>The chart is attached as an artifact (output/chart.png).</p>',
            ),
            node(
                "email",
                "output",
                COL * 3,
                label="Send email",
                output_kind="text",
                output_source="{{steps.html.output}}",
                deliver_tool="send_email",
                deliver_args={
                    "to": "{{input.recipients}}",
                    "subject": "{{input.title}}",
                    "body": "{{output}}",
                    "html": True,
                },
            ),
        ],
        "edges": [edge("query", "chart"), edge("chart", "html"), edge("html", "email")],
    },
    # ------------------------------------------------------------------ 6
    {
        "key": "meeting-notes",
        "name": "Meeting notes",
        "description": "Upload audio → transcribe → summary + action items → a Notion task per action item.",
        "input_schema": [
            field(
                "audio", "file", required=True, description="mp3 / wav / m4a / webm, up to 25 MB"
            ),
            field(
                "notion_database_id",
                label="Notion tasks database ID",
                placeholder="leave empty to skip",
            ),
        ],
        "sandbox": {"network": "none"},
        "nodes": [
            node(
                "transcribe",
                "custom",
                0,
                label="Transcribe",
                tools_allowed=["transcribe_audio"],
                max_turns=2,
                system_prompt="Transcribe the audio at {{input.audio.path}} with transcribe_audio and return the transcript unchanged.",
            ),
            node(
                "summarize",
                "custom",
                COL,
                label="Summary + actions",
                max_turns=2,
                system_prompt='From the transcript return JSON only: {"summary": "5-8 sentence summary", "decisions": [str], "action_items": [{"title": str, "owner": str|null, "due": "YYYY-MM-DD"|null}]}.\n\nTranscript:\n{{prev_output}}',
            ),
            node(
                "each",
                "loop",
                COL * 2,
                label="For each action item",
                items_expression="steps.summarize.data.action_items",
                on_fail="retry",
            ),
            node(
                "task",
                "custom",
                COL * 3,
                label="Create Notion task",
                tools_allowed=["notion"],
                max_turns=3,
                system_prompt="If the Notion database id '{{input.notion_database_id}}' is empty reply SKIPPED. Otherwise create a page in that database with title '{{items.current.title}}' and text 'Owner: {{items.current.owner}}, due: {{items.current.due}}' using the notion tool; reply with the page URL.",
            ),
            node(
                "notes",
                "template",
                COL * 4,
                label="Notes file",
                output_path="output/meeting-notes.md",
                template="# Meeting notes\n\n{{ steps.summarize.data.summary }}\n\n## Decisions\n{% for d in steps.summarize.data.decisions %}- {{ d }}\n{% endfor %}\n## Action items\n{% for a in steps.summarize.data.action_items %}- [ ] {{ a.title }} ({{ a.owner or 'unassigned' }}, due {{ a.due or 'n/a' }})\n{% endfor %}",
            ),
            node(
                "result",
                "output",
                COL * 5,
                label="Notes",
                output_kind="json",
                output_source="{{steps.summarize.output}}",
            ),
        ],
        "edges": [
            edge("transcribe", "summarize"),
            edge("summarize", "each"),
            edge("each", "task", "body"),
            edge("task", "each"),
            edge("each", "notes", "done"),
            edge("notes", "result"),
        ],
    },
]
