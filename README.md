# FlowForge

Nền tảng tự host để thiết kế và chạy **AI workflow**: người dùng vẽ một *Flow* gồm nhiều *Step*
(agent LLM có tool, script, HTTP, điều kiện, vòng lặp, duyệt tay…), chạy nó trên repo Git hoặc
workspace trống trong **sandbox Docker**, theo dõi log real-time, lấy kết quả/artifact và xem lịch
sử. Multi-user, multi-LLM, có skill/persona/rule, trigger (cron, webhook, email, thư mục), tool
plugin và MCP.

- [Kiến trúc](#kiến-trúc)
- [Chạy nhanh](#chạy-nhanh)
- [Biến môi trường](#biến-môi-trường)
- [Khái niệm](#khái-niệm)
- [Executor hoạt động thế nào](#executor-hoạt-động-thế-nào)
- [Step type](#step-type)
- [Biến và ghép prompt](#biến-và-ghép-prompt)
- [Skills và Persona](#skills-và-persona)
- [Rules](#rules)
- [Multi-LLM và chi phí](#multi-llm-và-chi-phí)
- [Tool plugin và MCP](#tool-plugin-và-mcp)
- [Input schema, upload và Trigger](#input-schema-upload-và-trigger)
- [Sandbox: giới hạn, network allowlist, artifact](#sandbox-giới-hạn-network-allowlist-artifact)
- [Templates, Import/Export, Chia sẻ](#templates-importexport-chia-sẻ)
- [API](#api)
- [Frontend](#frontend)
- [Mở rộng](#mở-rộng)
- [Bảo mật](#bảo-mật)
- [Lộ trình](#lộ-trình)

## Kiến trúc

```
/backend   FastAPI + SQLAlchemy 2 (SQLite; Postgres qua DATABASE_URL) + WebSocket
           app/routers   REST + WS           app/executor  engine, agent loop, sandbox, nodes
           app/services  runs, triggers,     app/tools     tool plugin registry (code, web, files,
                         skills, rules,                    data, communication, integrations, media, mcp)
                         flowio, artifacts   app/llm       LLMProvider (anthropic, openai, ollama, openai_compatible)
/frontend  React 19 + TypeScript + Vite + Tailwind 4 + React Flow 12
/sandbox   Dockerfile của container chạy step (Python/Node/tesseract/ffmpeg) + proxy.py cho allowlist
docker-compose.yml
```

## Chạy nhanh

```bash
cp .env.example .env        # điền JWT_SECRET (bắt buộc khi chạy thật) và key LLM nếu dùng key server
docker compose up --build   # build sandbox image, backend (8000) và frontend (5173)
```

- Frontend: http://localhost:5173 — đăng ký tài khoản đầu tiên tại `/register`.
- Backend API: http://localhost:8000 (OpenAPI: http://localhost:8000/docs).
- Thêm API key LLM tại **Settings → API keys**, tạo model tại **Settings → Models**, rồi tạo flow từ
  **Templates**.

Phát triển không dùng Docker cho backend/frontend (vẫn cần Docker daemon cho sandbox):

```bash
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" && uvicorn app.main:app --reload --port 8000 && pytest
cd frontend && npm install && npm run dev && npm test
docker build -t flowforge-sandbox:latest sandbox/      # image sandbox
```

## Biến môi trường

Xem [.env.example](.env.example). Các biến quan trọng:

| Biến | Mô tả |
|---|---|
| `JWT_SECRET` | Secret ký JWT. **Bắt buộc** khi chạy thật; để trống thì sinh ngẫu nhiên mỗi lần khởi động (mọi phiên đăng nhập mất khi restart). |
| `APP_SECRET` | Secret mã hoá API key/secret lưu trong DB (Fernet). Trống thì suy ra từ `JWT_SECRET`; đổi nó thì key đã lưu không giải mã được. |
| `ACCESS_TOKEN_MINUTES`, `REFRESH_TOKEN_DAYS` | Thời hạn access (15) / refresh (7 ngày) token. |
| `COOKIE_SECURE` | `true` khi chạy sau HTTPS để cookie có cờ Secure. |
| `REGISTRATION` | `open` (mặc định) \| `first_user` (chỉ tài khoản đầu tiên đăng ký được) \| `closed`. Tài khoản đầu tiên là **admin**. |
| `LOGIN_MAX_FAILURES`, `LOGIN_WINDOW_SECONDS` | Giới hạn đăng nhập sai theo (IP, email) → 429. |
| `TRUSTED_INTERNAL_URLS` | Tiền tố URL nội bộ backend được phép gọi cho user (mặc định Ollama local); mọi URL khác phải trỏ tới địa chỉ công khai. |
| `MCP_STDIO_ADMIN_ONLY` | `true`: MCP server kiểu `command` chỉ admin được tạo (chạy process trên host). |
| `CORS_ORIGINS` | Origin frontend được phép gọi API (phân cách bằng dấu phẩy). |
| `PUBLIC_BASE_URL` | URL công khai của backend (hiển thị URL webhook đúng khi chạy sau reverse proxy). |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` | Key server dùng chung (fallback khi user chưa có key riêng). |
| `ANTHROPIC_MODEL`, `ANTHROPIC_EFFORT`, `ANTHROPIC_FALLBACKS` | Model Anthropic mặc định khi user chưa tạo model nào; effort `low…max`; refusal fallback. |
| `PRICING_FILE` | Bảng giá USD/1M token để ước tính chi phí (sửa được trong app). |
| `DATABASE_URL` | SQLAlchemy URL. Mặc định SQLite `./data/flowforge.db`. |
| `WORKSPACE_DIR`, `HOST_WORKSPACE_DIR` | Nơi clone repo/workspace mỗi run; đường dẫn tương ứng nhìn từ Docker daemon (khi backend chạy trong container). |
| `SANDBOX_IMAGE`, `SANDBOX_NETWORK`, `SANDBOX_MAX_NETWORK` | Image sandbox; network mặc định và **trần** mà flow được phép yêu cầu (`none` < `allowlist` < `bridge`, trần mặc định `allowlist`). |
| `SANDBOX_MAX_CPUS`, `SANDBOX_MAX_MEMORY_MB`, `SANDBOX_MAX_TIMEOUT_SECONDS` | Trần cho cấu hình sandbox của từng flow. |
| `MAX_STEPS_PER_RUN` | Giới hạn số lần chạy step trong một run (chặn vòng lặp vô hạn). |
| `SHELL_WHITELIST` | Lệnh mà tool `run_shell` và step `shell` được phép chạy. |
| `ARTIFACTS_DIR`, `ARTIFACT_MAX_BYTES`, `ARTIFACT_MAX_FILES` | Lưu file dưới `/repo/output` sau mỗi run. |
| `UPLOADS_DIR`, `WATCH_DIR`, `SKILLS_DIR` | File upload làm input, thư mục theo dõi của trigger `file_watch`, cache skill trước khi mount. |
| `TRIGGER_POLL_SECONDS` | Chu kỳ scheduler quét cron / IMAP / thư mục (mặc định 30s). |
| `GITHUB_TOKEN` | Dành cho tạo Pull Request (chưa bật). |
| `VITE_API_URL` | URL backend mà frontend gọi (build-time). |

## Khái niệm

| Khái niệm | Ý nghĩa |
|---|---|
| **Flow** | Graph React Flow gồm `nodes` (step) và `edges`; có `input_schema`, `sandbox`, `visibility`. Thuộc về một user. |
| **Step** | Một node. Step LLM (`plan`, `code`, `test`, `review`, `custom`) là agent loop có tools; step khác chạy không cần model. |
| **Run** | Một lần chạy flow: workspace riêng, sandbox riêng, log stream, `StepRun` cho từng step, outputs, artifacts, diff, chi phí. |
| **Skill / Persona** | Gói `SKILL.md` + file tham khảo gắn vào step: skill nói *làm thế nào*, persona nói *agent là ai*. |
| **Rule** | Ràng buộc ngắn ghép vào đầu prompt theo scope global / flow / step. |
| **Model** | Cấu hình LLM của user (provider, tên model, key, params); mỗi step chọn model và model fallback. |
| **Tool** | Hàm agent gọi được (đọc/ghi file, shell, web, tài liệu, SQL, email, Slack, Notion… hoặc tool từ MCP server). |
| **Trigger** | Cách khởi chạy tự động: cron, webhook, GitHub event, email IMAP, thư mục theo dõi. |
| **Template** | Flow mẫu (built-in hoặc của user) kèm input schema, sandbox, skill và rule. |

## Executor hoạt động thế nào

1. `POST /flows/{id}/run` validate `inputs` theo schema, ghi `Run` và chạy `FlowExecutor` nền.
2. Executor resolve mọi model của các step LLM (thiếu key → thất bại ngay, có thông báo), chuẩn bị
   workspace: clone `repo_url` (`--depth 1`, nhánh `flowforge/<run_id>`) hoặc tạo workspace Git
   trống có `input/` và `output/`; chép file upload vào `/repo/input/`.
3. Khởi động container sandbox từ `SANDBOX_IMAGE` với workspace bind-mount tại `/repo`, user không
   phải root, `--cap-drop ALL`, giới hạn CPU/RAM/PID, network theo cấu hình; mount skill vào
   `/skills/<name>/`.
4. Đi graph từ node duy nhất không có edge vào. Mỗi step ghi `StepRun` (messages, tool calls,
   token, chi phí, skill đã dùng) và phát event qua WebSocket. Thất bại → `on_fail`: `retry`,
   `goto:<step>`, `stop`; `retry_limit` giới hạn số lần. `MAX_STEPS_PER_RUN` và
   `timeout_seconds` của sandbox chặn run chạy mãi.
5. Kết thúc: lưu `final_diff`, chép `/repo/output` thành artifact, xoá container/network/workspace.

Mọi lệnh trong sandbox đi qua `docker exec` với argv (không qua shell). Tool `run_shell` và step
`shell` chỉ chạy executable trong `SHELL_WHITELIST` và từ chối ký tự đặc biệt; đường dẫn file
được chuẩn hoá và phải nằm trong `/repo`.

## Step type

| Type | Dùng LLM | Cấu hình chính | Hành vi |
|---|---|---|---|
| `plan`, `code`, `test`, `review` | ✔ | `system_prompt` (trống = prompt mặc định), `tools_allowed`, `max_turns`, `model_id`, `fallback_model_id`, `skill_ids` | Agent loop; output của step trước đưa vào prompt step sau |
| `custom` | ✔ | như trên, prompt bắt buộc | Dùng đúng prompt bạn viết, có biến `{{…}}` |
| `shell` | – | `command` | Chạy lệnh whitelist, thành công khi exit 0 |
| `condition` | – | `expression` | Rẽ nhánh handle `true` / `false` theo kết quả step trước |
| `data_transform` | – | `language` python/javascript, `code` | Script trong sandbox với `input`, `steps`, `items`, `prev`, `task`, hàm `emit(value)`; JSON emit được thành `steps.<id>.data` |
| `http_request` | – | `url`, `method`, `headers`, `body` | Gọi từ backend; `steps.<id>.data = {status, body, headers}`; ≥ 400 → fail |
| `loop` | – | `items_expression` | Handle **body** chạy cho từng phần tử (`items.current/index/total`), step cuối body nối ngược về loop; handle **done** đi tiếp với danh sách kết quả |
| `merge` | – | `merge_steps[]`, `merge_mode` concat/json | Gộp output các step |
| `template` | – | `template` (Jinja2 sandboxed), `output_path` | Render text hoặc ghi file trong `/repo` |
| `output` | – | `output_kind` text/json/file, `output_source`, `deliver_tool`, `deliver_args` | Khai báo kết quả cuối (`Run.outputs`, tab Outputs) và tuỳ chọn gửi đi bằng tool |
| `approval` | – | `system_prompt` (câu hỏi), `approval_timeout_seconds` | Run chuyển `waiting`; duyệt ở trang Run hoặc `POST /runs/{id}/approve`; reject/hết hạn → fail theo `on_fail` |

**Biểu thức condition**: tên `success`, `output`, `exit_code`, `type`, `step_id`; hàm
`contains`, `icontains`, `startswith`, `endswith`, `matches(regex)`, `len`. Ví dụ
`contains("RESULT: PASS")`, `exit_code == 0 and not icontains("error")`.

**Validate graph** (`POST /flows/validate`, cũng chạy khi lưu): lỗi khi trùng id, edge treo, goto
sai, condition/loop sai handle, shell thiếu command, không có đúng một node start, chu trình không
đi qua condition/loop; cảnh báo khi node không nối, không tới được, custom chưa có prompt…

## Biến và ghép prompt

Biến dùng được trong prompt, URL, header, body, template, deliver args: `{{task}}`, `{{repo}}`,
`{{branch}}`, `{{step_id}}`, `{{prev_output}}`, `{{input.<field>}}`, `{{steps.<id>.output}}`,
`{{steps.<id>.data.<path>}}`, `{{steps.<id>.success}}`, `{{items.current}}`, `{{items.index}}`,
`{{items.total}}`. Executor phát event `variables` sau mỗi step; tab **Variables** của trang Run
hiển thị ngữ cảnh này.

System prompt của một step LLM được ghép theo thứ tự cố định
(`app/executor/prompts.py::system_prompt_for`):

```
base rules → rules global → rules flow → rules step → persona → skills → system_prompt của step
```

`GET /flows/{id}/steps/{step_id}/resolved-prompt` trả prompt đã ghép kèm từng section; nút
**Composed prompt** trong editor hiển thị nó. **Try this step** chạy thử một step với input giả
lập (tools tắt, một lượt model) và cũng dùng đúng prompt này.

## Skills và Persona

Một skill là thư mục `SKILL.md` (front matter `name`, `description`, `kind`, `tools_required[]` +
nội dung markdown) và file tham khảo/script tuỳ chọn. Skill lưu trong bảng `skills` (file lưu
inline base64), khi chạy được cache ra `SKILLS_DIR/<id>/` và copy vào sandbox tại
`/skills/<name>/` để agent đọc. Giới hạn: file ≤ 1 MB, tổng ≤ 5 MB, ≤ 40 file.

```
my-skill/
├── SKILL.md          # ---\nname: my-skill\ndescription: ...\ntools_required: [read_file]\n---\n# nội dung
├── checklist.md      # agent đọc ở /skills/my-skill/checklist.md
└── scripts/run.py
```

`kind` là `skill` (làm việc này thế nào) hoặc `persona` (agent là ai: vai trò, giọng điệu, tiêu
chuẩn). Mỗi step chọn tối đa một persona và nhiều skill (`skill_ids`); trong prompt persona đứng
trước skill. Built-in (chỉ đọc, bấm **Duplicate** để sửa):

- Skill: `python-testing`, `react-component`, `git-workflow`, `code-review-checklist`,
  `security-audit`, `brand-voice-template`, `citation-format`, `data-analysis-checklist`,
  `email-tone-guide`.
- Persona: `researcher`, `copywriter`, `data-analyst`, `support-agent`, `document-extractor`.

Trang **Skills** có editor markdown với preview, upload file, import zip (`SKILL.md` ở gốc hoặc
trong một thư mục), export zip. Trong editor, sidebar step LLM có dropdown Persona và multi-select
Skills; node hiện badge.

## Rules

Rule là đoạn text ngắn trong bảng `rules` (`name`, `content`, `scope` global | flow | step,
`flow_id`, `step_id`, `priority`, `enabled`). 4 rule built-in được **sao chép vào tài khoản mỗi
user** lần đầu đọc `/rules`, nên ai cũng sửa, tắt, xoá bản của mình: `no-secrets`,
`always-write-tests`, `follow-claude-md` (bật sẵn), `vietnamese-comments` (tắt sẵn). Trong mỗi
scope rule sắp theo `priority` tăng dần. Xoá flow sẽ xoá rule của flow.

Giao diện: trang **Rules** cho rule global; nút **Rules** trong editor cho rule của flow và của
từng step.

## Multi-LLM và chi phí

Mọi lời gọi model đi qua `app/llm/` với interface `LLMProvider.complete(messages, tools, system,
max_tokens) -> Completion`. Format message nội bộ là dạng Anthropic; mỗi provider tự chuyển sang
wire format của mình và tool JSON-schema được chuẩn hoá nên tool-calling chạy trên mọi provider.

| Provider | Ghi chú |
|---|---|
| `anthropic` | SDK chính thức, refusal fallback, `effort` |
| `openai` | `/chat/completions` qua `httpx` |
| `ollama` | `http://localhost:11434/v1`, không cần key |
| `openai_compatible` | `base_url` + key: Groq, DeepSeek, OpenRouter, vLLM… |

**Bảng `models`** (Settings → Models): `provider`, `model_name`, `label`, `base_url`, `api_key_id`
(trống = key mới nhất cùng loại của user, rồi key server), `params` (`temperature`, `max_tokens`,
`effort`), `is_default`. Step có `model_id` (trống = model mặc định) và `fallback_model_id`: khi
provider lỗi (401/429/5xx/mạng) step chạy lại một lần với model fallback.

**Chi phí**: mỗi `StepRun` lưu `model`, `input_tokens`, `output_tokens`, `cost_usd`; `Run.cost_usd`
là tổng. Bảng giá ở `PRICING_FILE` (USD/1M token, key `provider/model`, khớp tiền tố dài nhất),
sửa qua `GET/PUT /pricing`. Trang Run hiện model, persona/skill, token và chi phí từng step;
Dashboard có tổng chi và biểu đồ **Cost by model** (`/stats`).

**API key của user** (Settings → API keys): mã hoá Fernet bằng `APP_SECRET`, API chỉ trả 4 ký tự
cuối; `POST /keys/test` kiểm tra key trước khi lưu. Provider: `anthropic`, `openai`, `github`,
`tavily`, `smtp`, `slack`, `telegram`, `notion`, `google`, `database`, `imap`, `other`.

## Tool plugin và MCP

Tool nằm trong `backend/app/tools/`, đăng ký vào `registry` khi import. Mỗi tool là lớp con của
`Tool` với `name`, `description`, `input_schema` (JSON schema, `additionalProperties: false`),
`category`, `required_credentials`, `needs_sandbox` và `async run(args, ctx)`.

| Category | Tools | Credential |
|---|---|---|
| code | `read_file`, `write_file`, `list_files`, `run_shell`, `git_diff` | – (sandbox) |
| web | `fetch_url`, `web_search` (Tavily hoặc DuckDuckGo), `scrape` (readability) | `tavily` (tuỳ chọn) |
| files | `read_document` (PDF/DOCX/XLSX/CSV), `write_document` (DOCX/XLSX/PDF), `ocr_image` | – |
| data | `query_sql` (chỉ SELECT/WITH, giới hạn dòng), `run_python` (trong sandbox) | `database` |
| communication | `send_email` (SMTP), `slack_post`, `telegram_send` | `smtp`, `slack`, `telegram` |
| integrations | `google_sheets`, `notion`, `github_issue`, `webhook_call` | `google`, `notion`, `github` |
| media | `generate_image`, `transcribe_audio` | `openai` |

**Viết tool mới**:

```python
# backend/app/tools/mytools.py
from app.tools.base import Tool, ToolCategory, ToolContext, ToolError, schema

class Shout(Tool):
    name = "shout"
    description = "Upper-case some text"
    category = ToolCategory.code
    input_schema = schema({"text": {"type": "string"}}, ["text"])

    async def run(self, args, ctx: ToolContext) -> str:
        return args["text"].upper()
```

Thêm instance vào `BUILTIN_TOOLS` trong `app/tools/__init__.py`. Trong `run()`: `ctx.need("provider")`
lấy credential (lỗi rõ nếu thiếu), `ctx.sandbox_required()` chạy lệnh/đọc ghi file trong sandbox,
`ctx.client()` cho HTTP. Ném `ToolError` khi thất bại. Ghi đè `self_test(ctx)` cho nút **Test**.

**MCP server**: Tools → Add MCP server, chọn HTTP (Streamable HTTP, headers mã hoá) hoặc Command
(stdio, env mã hoá). Backend gọi `tools/list`, cache vào `mcp_servers.tools_cache`; tool có tên
`mcp:<server_id>:<tool>` và gắn vào step như tool thường. `GET /tools` liệt kê kèm `status`
(`ready` / `missing_credentials` / `offline`).

## Input schema, upload và Trigger

**Input schema**: flow có danh sách field `{name, label, type, required, default, options,
placeholder, description}` với `type` ∈ `text | textarea | number | url | select | file | boolean`.
Soạn trong editor (nút **Inputs**); trang Run sinh form; giá trị gửi trong `POST /flows/{id}/run`
dưới `inputs` và được validate (422 nếu sai). Field `file`: upload trước qua `POST /uploads`
(multipart, ≤ 100 MB) rồi gửi `{"upload_id", "name"}`; khi chạy file nằm ở `/repo/input/<tên>`.
`repo_url` là tuỳ chọn.

**Trigger** (bảng `triggers`, quản lý trong editor nút **Triggers** hoặc API):

| Type | Config | Cách hoạt động |
|---|---|---|
| `schedule` | `cron` (5 trường, UTC) | Scheduler quét mỗi `TRIGGER_POLL_SECONDS`, bắn khi tới giờ |
| `webhook` | – | `POST /hooks/{trigger_id}`; body JSON → `inputs` (`task` đặt task), payload gốc ở `input.payload` |
| `github` | `events[]` | Như webhook, xác thực `X-Hub-Signature-256`, lọc `X-GitHub-Event`, có `input.event` |
| `email` | `folder`, `poll_seconds` (≥ 30), `unseen_only` | Poll IMAP bằng credential `imap` (`imap://user:pass@host:993`); mỗi mail → run với `input.email.{subject,from,to,body,message_id}` |
| `file_watch` | `pattern` | Thả file vào `WATCH_DIR/<user>/<trigger>`; file → upload → run với `input.file`, file gốc chuyển sang `processed/` |

**Bảo mật webhook**: endpoint công khai nhưng mọi request phải mang
`X-FlowForge-Signature: sha256=<HMAC-SHA256(secret, raw body)>` (hoặc GitHub
`X-Hub-Signature-256`, hoặc `Authorization: Bearer <secret>`). Secret 32 byte ngẫu nhiên, lưu mã
hoá, chỉ hiển thị cho chủ flow, xoay được. So sánh bằng `hmac.compare_digest`; sai chữ ký → 401,
trigger không tồn tại/tắt → 404, body > 1 MB → 413.

## Sandbox: giới hạn, network allowlist, artifact

**Image** ([sandbox/Dockerfile](sandbox/Dockerfile)): Python 3.12 + pandas, numpy, matplotlib,
openpyxl, pypdf, python-docx, requests, beautifulsoup4, pillow; Node 22; git; tesseract (eng, vie);
ffmpeg; user `runner`, thư mục `/repo` và `/skills`.

**Giới hạn theo flow** (nút **Sandbox**, lưu ở `Flow.sandbox`, override được khi tạo run): `cpus`,
`memory_mb`, `timeout_seconds` (toàn bộ run), `network`, `allowed_hosts`. Server kẹp theo
`SANDBOX_MAX_*`.

**Network**: `none` (mặc định, không có egress), `bridge` (mở), `allowlist`: backend tạo Docker
network `--internal` riêng cho run, chạy sidecar proxy ([sandbox/proxy.py](sandbox/proxy.py)) gắn
cả vào network nội bộ lẫn `bridge`, đặt `HTTP_PROXY`/`HTTPS_PROXY` cho sandbox; proxy chỉ cho
CONNECT/HTTP tới host khớp danh sách (`example.com`, `*.example.com`), còn lại 403. Mọi thứ bị dọn
khi run kết thúc.

**Artifact**: file dưới `/repo/output` được chép ra `ARTIFACTS_DIR/<run_id>/` (giới hạn
`ARTIFACT_MAX_BYTES`, `ARTIFACT_MAX_FILES`), lưu bảng `artifacts`; tải qua
`GET /runs/{id}/artifacts/{artifact_id}/download` hoặc tab **Artifacts**.

## Templates, Import/Export, Chia sẻ

**Templates**: bảng `flow_templates` (graph, `input_schema`, `sandbox`, `skills` theo tên,
`rules`). 9 template built-in seed khi khởi động (`app/templates.py`, `app/templates_extra.py`;
built-in được đồng bộ lại theo code mỗi lần khởi động):

| Key | Mục đích | Persona / skill gắn sẵn |
|---|---|---|
| `fix-issue`, `add-feature`, `refactor-review` | Coding trên repo | – |
| `research-report` | Chủ đề → web search → đọc nguồn → tổng hợp có trích dẫn → DOCX | researcher, citation-format |
| `blog-pipeline` | Brief → outline → draft → reviewer brand-voice (loop) → ảnh → Notion | copywriter, brand-voice-template |
| `inbox-triage` | Email → phân loại → soạn trả lời → **duyệt tay** → gửi | support-agent, email-tone-guide |
| `invoice-extractor` | PDF/ảnh → OCR → JSON → validate → Google Sheets → Slack | document-extractor |
| `weekly-data-report` | SQL → biểu đồ → HTML → email (gắn trigger `schedule`) | data-analyst, data-analysis-checklist |
| `meeting-notes` | Audio → transcript → tóm tắt + action items → task Notion | – |

**Save as template** ghi lại tên skill và rule của flow; **Use template** ghép tên về id skill của
bạn và tạo rule cho flow mới.

**Export/Import**: `GET /flows/{id}/export` trả JSON (`flowforge: 2`) gồm graph, inputs, sandbox,
model tham chiếu theo label (**không kèm key**), skill/persona (nội dung + file) và rule.
`POST /flows/import` tạo flow mới, `POST /flows/{id}/import` thay thế flow hiện có; trả
`{flow, warnings[]}`: model ghép theo label (thiếu → model mặc định + cảnh báo), skill ghép theo tên
(thiếu → tạo từ tài liệu), rule tạo lại. Nút **Export/Import** trong editor, **Import** ở trang
Flows. Duplicate flow cũng sao chép rule.

**Chia sẻ**: `visibility` `private` | `shared_link`. Bật trong nút **Share**; `GET /shared/{token}`
xem công khai (không lộ chủ sở hữu, không lộ key/rule), `POST /shared/{token}/clone` cần đăng nhập
và tạo bản sao private. Tắt chia sẻ thì link ngừng hoạt động.

## API

Mọi route trừ `/auth/register|login|refresh`, `/shared/{token}`, `/hooks/{id}`, `/health` yêu cầu
đăng nhập (cookie httpOnly hoặc `Authorization: Bearer <access_token>`). Tài nguyên của user khác
trả 404. OpenAPI đầy đủ tại `/docs`.

| Nhóm | Route |
|---|---|
| Auth | `POST /auth/register`, `/auth/login`, `/auth/logout`, `/auth/refresh`, `/auth/password`; `GET /auth/me` |
| Flows | `GET/POST /flows`, `GET/PUT/DELETE /flows/{id}`, `POST /flows/validate`, `/flows/{id}/duplicate`, `GET /flows/{id}/export`, `POST /flows/import`, `/flows/{id}/import`, `POST /flows/{id}/steps/{step}/try`, `GET /flows/{id}/steps/{step}/resolved-prompt` |
| Runs | `POST /flows/{id}/run` (202), `GET /runs?flow_id=&status=&limit=`, `GET /runs/{id}`, `/runs/{id}/steps`, `POST /runs/{id}/cancel`, `/runs/{id}/approve`, `GET /runs/{id}/artifacts`, `/runs/{id}/artifacts/{aid}/download`, `WS /runs/{id}/logs` |
| Triggers | `GET/POST /flows/{id}/triggers`, `PUT/DELETE /triggers/{id}`, `POST /triggers/{id}/fire`, `POST /hooks/{id}` (công khai, có chữ ký), `POST /uploads` |
| Skills | `GET/POST /skills`, `GET/PUT/DELETE /skills/{id}`, `POST /skills/{id}/duplicate`, `/skills/import`, `GET /skills/{id}/export`, `POST /skills/{id}/files`, `GET/DELETE /skills/{id}/files/{path}` |
| Rules | `GET/POST /rules`, `GET/PUT/DELETE /rules/{id}` |
| Models | `GET/POST /models`, `PUT/DELETE /models/{id}`, `POST /models/{id}/default`, `/models/{id}/test`, `GET/PUT /pricing` |
| Keys | `GET/POST /keys`, `DELETE /keys/{id}`, `POST /keys/test`, `/keys/{id}/test` |
| Tools | `GET /tools`, `POST /tools/{name}/test`, `GET/POST /mcp-servers`, `PUT/DELETE /mcp-servers/{id}`, `POST /mcp-servers/{id}/refresh` |
| Templates | `GET/POST /templates`, `POST /templates/{id}/use`, `DELETE /templates/{id}` |
| Khác | `GET /stats`, `GET /shared/{token}`, `POST /shared/{token}/clone`, `GET /health` |

**WebSocket log**: `ws://host/runs/{id}/logs` gửi JSON từng event và kết thúc bằng
`{"event": "end"}`; run không tồn tại → close `4404`, chưa đăng nhập → `4401`. Run đang chạy: gửi
lại lịch sử đã buffer rồi stream live; run đã kết thúc: dựng lại từ DB với `"replayed": true`.
Event: `run_status`, `step_start`, `text`, `tool_call`, `tool_result`, `step_end`, `log`,
`variables`, `output`, `artifacts`, `approval_requested`, `approval_resolved`, `end`.

## Frontend

```
frontend/src/
  api/         client.ts (fetch wrapper, cookie auth, tự refresh khi 401), types.ts
  lib/         steps.ts (metadata step), runLog.ts (reducer WS → view), flowio.ts, markdown.ts,
               models.ts, ModelsContext / ToolsContext / SkillsContext
  hooks/       useRunLogs
  components/  StepNode, StepSidebar, TryStepPanel, ResolvedPromptModal, RulesEditor,
               InputForm, InputSchemaEditor, TriggersPanel, SandboxSettingsEditor,
               LogPanel, DiffView, CostByModelChart, ui, layout/(Sidebar, Icons)
  pages/       Dashboard, Flows, FlowEditor, Run, RunDetail, History, Templates, Tools,
               Skills, Rules, SharedFlow, auth/(Login, Register),
               settings/(Keys, Models, Profile)
  router.tsx   /, /flows, /flows/:id, /flows/:id/run, /runs, /runs/:id, /templates, /tools,
               /skills, /rules, /settings/{keys,models,profile}, /login, /register, /shared/:token
```

Sidebar thu gọn được, dark mode (light → dark → system qua `data-theme`), drawer trên mobile. Trang
Run detail có tab Logs (card theo step, tool call gập mở), Variables, Outputs, Artifacts, Diff,
Steps và thẻ duyệt khi run `waiting`. History lọc theo flow/status và tự refresh khi còn run chạy.

### Giao diện chủ cơ sở (module ngành)

Người dùng có cơ sở (workspace) thấy menu tiếng Việt: **Tổng quan** (`/`, số liệu từ `GET /biz/overview`:
lịch hôm nay, tỉ lệ không đến 30 ngày, gói sắp hết hạn, tin chưa trả lời, giờ tiết kiệm = số tin AI đã
gửi trong tuần × 1,5 phút), **Hộp thư** (`/inbox`, Zalo + Messenger gộp; gợi ý AI có Gửi / Sửa / Bỏ,
nút "Tôi tiếp quản" tắt AI cho hội thoại đó, thẻ khách bên phải, cảnh báo cửa sổ 24h), **Lịch**
(`/calendar`, xem ngày theo nhân viên hoặc tuần, lọc chi nhánh, kéo thả để đổi giờ / nhân viên, bấm
để đánh dấu đã đến / không đến, đặt lịch nhanh), Khách hàng, Gói & dịch vụ, Việc tự động, Kết nối và
mục Cài đặt (Cơ sở, Trợ lý AI, Model AI, API Keys, Tài khoản). Các trang kỹ thuật (Dashboard, Flows,
Runs, Templates, Tools, Skills, Rules) chỉ hiện và chỉ mở được với admin hệ thống (`is_admin`), được
bọc bởi `RequireAdmin` trong `router.tsx`.

### Gói, thanh toán, cảnh báo và quản trị

- **Gói** (`backend/app/billing/plans.py`): Dùng thử 30 ngày (100 hội thoại AI/tháng, 5 chi nhánh) →
  Cơ bản 499.000đ (500 hội thoại, 1 chi nhánh) → Pro 1.290.000đ (3.000 hội thoại, 5 chi nhánh).
  Hội thoại AI được đếm theo số hội thoại khác nhau mà AI đã trả lời trong tháng dương lịch.
  Hết hạn hoặc hết hạn mức thì AI ngừng trả lời (nhân viên vẫn trả lời tay), việc tự động theo lịch
  không chạy; gói trả phí có 7 ngày ân hạn. Vượt số chi nhánh bị chặn khi lưu Cơ sở (402).
- **Thanh toán** (`/biz/billing`): chủ cơ sở chọn gói và số tháng, hệ thống tạo hoá đơn với mã nội
  dung chuyển khoản `FFxxxxxx`, mã VietQR (payload EMVCo NAPAS + ảnh `img.vietqr.io`) và hoá đơn PDF
  (fpdf2, font DejaVu nếu có). Admin xác nhận đã nhận tiền trong trang Quản trị thì gói được kích hoạt
  (cộng dồn vào thời hạn hiện tại). Cổng thanh toán là interface `PaymentGateway` trong
  `app/billing/gateways.py`; thêm cổng mới bằng cách đăng ký vào `GATEWAYS`.
  Biến môi trường: `BILLING_BANK_BIN`, `BILLING_BANK_NAME`, `BILLING_BANK_ACCOUNT`,
  `BILLING_BANK_ACCOUNT_NAME`, `BILLING_TRIAL_DAYS`.
- **Cảnh báo** (`app/biz/alerts.py`): connector báo lỗi (test kết nối, đồng bộ lịch), việc tự động
  chạy lỗi, AI bị tạm dừng vì gói. Mỗi cảnh báo tạo thông báo cho chủ (Tổng quan + Zalo chủ nếu đã
  khai `owner_zalo_user_id`) và một bản cho admin (bảng `admin_alerts`, POST JSON tới
  `ALERT_WEBHOOK_URL` nếu cấu hình). Cùng một loại lỗi chỉ báo một lần mỗi 24 giờ.
- **Quản trị** (`/admin`, chỉ admin hệ thống): tổng quan số cơ sở, danh sách cơ sở với chủ, gói, ngày
  hết hạn thử, mức dùng, khách; nút Kéo dài thử, đặt gói tay, xem log (automation log, thông báo,
  run); hoá đơn chờ xác nhận; cảnh báo.

## Mở rộng

**Step type mới**: thêm giá trị vào `StepType` ([backend/app/schemas.py](backend/app/schemas.py));
step dùng LLM thì thêm prompt mặc định vào `DEFAULT_PROMPTS`
([prompts.py](backend/app/executor/prompts.py)) và `LLM_TYPES`; step không dùng LLM thì thêm hàm
trong [nodes.py](backend/app/executor/nodes.py) và nhánh dispatch trong `FlowExecutor._run_node`
([engine.py](backend/app/executor/engine.py)); nếu có handle rẽ nhánh thì khai báo trong
`BRANCHING_HANDLES`. Frontend: thêm vào `STEP_TYPES`/`STEP_META`/`defaultStepData`
([lib/steps.ts](frontend/src/lib/steps.ts), [api/types.ts](frontend/src/api/types.ts)) và các ô
cấu hình trong `StepSidebar`.

**Provider LLM mới**: lớp con của `LLMProvider` (hoặc `OpenAICompatibleProvider`) trong `app/llm/`,
thêm vào `PROVIDERS`, enum `ModelProvider` và `build_provider` (`app/services/models.py`); frontend
thêm nhãn ở `lib/models.ts`.

**Tool mới** và **MCP server**: xem [Tool plugin và MCP](#tool-plugin-và-mcp).

## Bảo mật

Những gì đã có:

- **Xác thực**: bcrypt cho mật khẩu (≥ 8 ký tự), JWT HS256 với `exp`, access token 15 phút và
  refresh token 7 ngày trong cookie `httpOnly`, `SameSite=Lax` (`Secure` khi `COOKIE_SECURE=true`);
  refresh cookie chỉ gửi tới `/auth/refresh`. Đổi mật khẩu cần mật khẩu cũ.
- **Cách ly theo user**: mọi truy vấn flow, run, step, artifact, upload, key, model, trigger, skill,
  rule, template, MCP server đều lọc theo `user_id`; tài nguyên của người khác trả 404. Shared link
  chỉ lộ graph đã lọc (không lộ chủ sở hữu, key, rule, trigger). Built-in (skill, template) chỉ đọc.
- **Bí mật**: API key, secret webhook, header/env của MCP server mã hoá Fernet bằng `APP_SECRET`;
  API chỉ trả `last4`; export flow không kèm key; log/event của run không in credential.
- **Sandbox**: container riêng mỗi run, user không root, `--cap-drop ALL`, giới hạn CPU/RAM/PID và
  thời gian, network mặc định `none`, allowlist qua proxy trên network `--internal`; `docker exec`
  bằng argv (không shell); `run_shell` chỉ chạy lệnh whitelist, đường dẫn chuẩn hoá trong `/repo`;
  script của `data_transform`/`run_python` chạy trong sandbox, không trên backend.
- **Đầu vào**: Jinja2 `SandboxedEnvironment`; biểu thức condition là parser riêng không `eval`;
  zip skill kiểm tra đường dẫn, kích thước, số file; upload ≤ 100 MB; webhook body ≤ 1 MB, chữ ký
  HMAC so sánh thời gian hằng; `query_sql` chỉ cho SELECT/WITH; graph validate trước khi lưu.
- **Vòng lặp**: `MAX_STEPS_PER_RUN`, `retry_limit`, `max_turns`, `timeout_seconds` sandbox.
- **Phiên và tài khoản**: logout và đổi mật khẩu thu hồi mọi phiên (`token_version` trong JWT); giới hạn
  đăng nhập sai; chính sách đăng ký; tài khoản đầu tiên là admin (sửa bảng giá, tạo MCP stdio).
- **Ra ngoài từ backend**: mọi URL người dùng cung cấp (http_request, fetch_url, webhook_call, base_url
  model, MCP) phải resolve ra địa chỉ công khai, kiểm tra lại sau mỗi redirect; proxy allowlist có token
  riêng từng run và chặn địa chỉ private.
- **CSRF và header**: request thay đổi dữ liệu có `Origin` lạ bị từ chối; cookie refresh `SameSite=Strict`;
  `nosniff`, `X-Frame-Options`, CSP ở frontend.

Kết quả rà soát bước 8 và các điểm đã sửa: xem [SECURITY.md](SECURITY.md). Khuyến nghị khi triển
khai: đặt `JWT_SECRET`/`APP_SECRET` mạnh và cố định, `COOKIE_SECURE=true` sau HTTPS, `CORS_ORIGINS`
đúng origin, giới hạn ai được đăng ký (đặt sau reverse proxy có auth hoặc tắt `/auth/register`
sau khi tạo tài khoản), sao lưu `data/`, và nhớ rằng backend cần quyền Docker socket (tương đương
root trên host) nên chỉ chạy trên máy tin cậy.

## Lộ trình

- [x] 1. Khởi tạo monorepo, docker-compose, README khung
- [x] 2. Backend: models + CRUD flows + test
- [x] 3. Executor + tools + sandbox, test với mock
- [x] 4. WebSocket log stream
- [x] 5. Frontend: Flows list + Flow Editor
- [x] 6. Frontend: Run + History
- [ ] 7. Tích hợp GitHub tạo PR (nút **Create PR** đang tắt)
- [x] 8. Rà soát bảo mật, hoàn thiện README
- [x] Auth, API key, sidebar/dashboard/dark mode, custom flow (templates, biến, validate, try, import/export, share)
- [x] Multi-LLM per step, Skills/Persona, Rules, export kèm model/skill/rule, chi phí theo model
- [x] Tool plugin + MCP, input schema + trigger, node type mới + biến xuyên flow, sandbox mở rộng, template và persona/skill mẫu
