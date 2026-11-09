# FlowForge

Webapp để thiết kế và chạy các **AI coding pipeline**: người dùng tạo *Flow* gồm nhiều *Step*
(mỗi step là một agent loop gọi LLM với system prompt, tools và điều kiện rẽ nhánh), chạy flow
trên một repo Git trong sandbox Docker, theo dõi log real-time và xem lịch sử các lần chạy.

## Kiến trúc

```
/backend   FastAPI + SQLAlchemy (SQLite, đổi Postgres qua DATABASE_URL) + Anthropic SDK + WebSocket
/frontend  React + TypeScript + Vite + Tailwind + React Flow
/sandbox   Dockerfile của container chạy step (repo mount tại /repo)
docker-compose.yml
```

## Chạy nhanh

```bash
cp .env.example .env        # điền ANTHROPIC_API_KEY (và GITHUB_TOKEN nếu muốn tạo PR)
docker compose up --build
```

- Frontend: http://localhost:5173
- Backend API: http://localhost:8000 (docs: http://localhost:8000/docs)

## Biến môi trường

| Biến | Mô tả |
|---|---|
| `ANTHROPIC_API_KEY` | API key gọi Claude. Bắt buộc để chạy flow. |
| `ANTHROPIC_MODEL` | Model dùng cho mọi step (mặc định `claude-opus-5-5`). |
| `ANTHROPIC_FALLBACKS` | `true` (mặc định): bật server-side refusal fallback của Claude API. Đặt `false` khi dùng Bedrock/Vertex. |
| `ANTHROPIC_EFFORT` | `low` / `medium` / `high` / `xhigh` / `max`. Mặc định `medium`. Để trống = mặc định của model. |
| `DATABASE_URL` | SQLAlchemy URL. Mặc định SQLite; đổi sang `postgresql+psycopg://...` để dùng Postgres. |
| `WORKSPACE_DIR` | Thư mục clone repo cho từng run. |
| `HOST_WORKSPACE_DIR` | Đường dẫn của `WORKSPACE_DIR` nhìn từ Docker daemon (cần khi backend chạy trong container). |
| `SANDBOX_IMAGE` | Image Docker dùng làm sandbox. |
| `SANDBOX_NETWORK` | Network của sandbox: `none` (mặc định, không có egress) hoặc `bridge` nếu step cần cài dependency. |
| `MAX_STEPS_PER_RUN` | Giới hạn tổng số lần chạy step trong một run (chặn vòng lặp vô hạn). Mặc định 50. |
| `SHELL_WHITELIST` | Danh sách lệnh shell (phân cách bằng dấu phẩy) agent được phép chạy. |
| `CORS_ORIGINS` | Origin của frontend được phép gọi API. |
| `GITHUB_TOKEN` | Token để tạo Pull Request. |
| `VITE_API_URL` | URL backend mà frontend gọi tới. |

## API

| Method | Path | Mô tả |
|---|---|---|
| GET | `/flows` | Danh sách flow (tóm tắt) |
| POST | `/flows` | Tạo flow: `{name, description, nodes[], edges[]}` |
| GET | `/flows/{id}` | Chi tiết flow kèm graph |
| PUT | `/flows/{id}` | Cập nhật một phần; graph được validate lại |
| DELETE | `/flows/{id}` | Xóa flow và các run của nó |
| POST | `/flows/{id}/duplicate` | Nhân bản flow |
| POST | `/flows/{id}/run` | Chạy flow: `{repo_url, branch, task}` → trả về run (202) |
| GET | `/runs?flow_id=&status=&limit=` | Danh sách run, mới nhất trước |
| GET | `/runs/{id}` | Chi tiết run kèm `final_diff` |
| GET | `/runs/{id}/steps` | Các StepRun kèm toàn bộ `messages` và `tool_calls` |
| POST | `/runs/{id}/cancel` | Hủy run đang chạy |

### Cấu trúc graph

`nodes[]` là node React Flow với `data` là thuộc tính step:

```json
{
  "id": "test", "type": "step", "position": {"x": 0, "y": 0},
  "data": {
    "label": "Run tests", "type": "test",
    "system_prompt": "...", "tools_allowed": ["read_file", "run_shell"],
    "max_turns": 20, "on_fail": "goto:code", "retry_limit": 2,
    "command": null, "expression": null
  }
}
```

- `type`: `plan | code | test | review | shell | condition`.
- `on_fail`: `retry | stop | goto:<step_id>` (step_id phải tồn tại).
- `command`: chỉ dùng cho step `shell` (chạy lệnh trực tiếp, không gọi LLM).
- `expression`: chỉ dùng cho step `condition`, ví dụ `prev.success`; edge đi ra phải có
  `sourceHandle` là `"true"` hoặc `"false"`.

## Phát triển (không dùng Docker)

```bash
# backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
pytest

# frontend
cd frontend
npm install
npm run dev
npm test
```

## Executor hoạt động thế nào

1. `POST /flows/{id}/run` tạo bản ghi `Run` và khởi chạy `FlowExecutor` dưới dạng background task.
2. Backend clone repo (`git clone --depth 1`) vào `WORKSPACE_DIR/<run_id>`, rồi khởi động một container
   từ `SANDBOX_IMAGE` với repo bind-mount tại `/repo` (`--network none`, `--cap-drop ALL`,
   giới hạn RAM/CPU/PID). Executor tạo nhánh `flowforge/<run_id>` trong repo.
3. Bắt đầu từ node duy nhất không có edge vào, executor đi theo edge:
   - Step LLM (`plan | code | test | review`): agent loop gọi Claude với system prompt của step,
     thực thi tool call cho đến khi model dừng gọi tool hoặc hết `max_turns`.
     Output của step trước được đưa vào prompt của step sau.
   - Step `shell`: chạy `command` (phải trong whitelist), thành công khi exit code 0.
   - Step `condition`: đánh giá `expression` trên kết quả step trước (`prev.success`,
     `prev.output`, `prev.exit_code`, `prev.type`) và đi theo edge `true` / `false`.
   - Step thất bại → `on_fail`: `retry` chạy lại, `goto:<id>` nhảy sang step khác,
     `stop` kết thúc run. `retry_limit` giới hạn số lần retry/goto của mỗi step.
4. Mọi message, tool call, token và thời gian của từng step được lưu vào `StepRun`.
   Khi kết thúc, diff cuối cùng được lưu vào `Run.final_diff` và container bị xóa.

Mọi lệnh đều được thực thi bằng `docker exec` với argv, không qua shell, nên không có
chuỗi lệnh nào được shell diễn giải. Tool `run_shell` từ chối ký tự đặc biệt (`; | & > < $ \``...)
và chỉ cho phép executable trong `SHELL_WHITELIST`. Mọi đường dẫn file được chuẩn hoá và
phải nằm trong `/repo`.

## Thêm step type mới

1. Thêm giá trị vào enum `StepType` trong [backend/app/schemas.py](backend/app/schemas.py).
2. Nếu step dùng LLM: thêm prompt mặc định vào `DEFAULT_PROMPTS` trong
   [backend/app/executor/prompts.py](backend/app/executor/prompts.py). Executor sẽ tự dùng
   agent loop cho mọi type không phải `shell`/`condition`.
3. Nếu step không dùng LLM: thêm nhánh xử lý trong `FlowExecutor._run_step`
   ([backend/app/executor/engine.py](backend/app/executor/engine.py)) trả về `StepOutcome`.
4. Thêm tool mới (nếu cần): thêm spec vào `TOOL_SPECS` và method `tool_<name>` trong
   [backend/app/executor/tools.py](backend/app/executor/tools.py), rồi thêm tên vào `ToolName`
   trong `schemas.py`.
5. Frontend: thêm type vào danh sách trong sidebar của Flow Editor.

## Bảo mật

_(sẽ bổ sung ở bước rà soát)_

## Lộ trình

- [x] 1. Khởi tạo monorepo, docker-compose, README khung
- [x] 2. Backend: models + CRUD flows + test
- [x] 3. Executor + tools + sandbox, test với mock
- [ ] 4. WebSocket log stream
- [ ] 5. Frontend: Flows list + Flow Editor
- [ ] 6. Frontend: Run + History
- [ ] 7. Tích hợp GitHub tạo PR
- [ ] 8. Rà soát bảo mật, hoàn thiện README
