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
| `ANTHROPIC_MODEL` | Model dùng cho mọi step (mặc định `claude-sonnet-5-5`). |
| `DATABASE_URL` | SQLAlchemy URL. Mặc định SQLite; đổi sang `postgresql+psycopg://...` để dùng Postgres. |
| `WORKSPACE_DIR` | Thư mục clone repo cho từng run. |
| `SANDBOX_IMAGE` | Image Docker dùng làm sandbox. |
| `SHELL_WHITELIST` | Danh sách lệnh shell (phân cách bằng dấu phẩy) agent được phép chạy. |
| `CORS_ORIGINS` | Origin của frontend được phép gọi API. |
| `GITHUB_TOKEN` | Token để tạo Pull Request. |
| `VITE_API_URL` | URL backend mà frontend gọi tới. |

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

## Thêm step type mới

_(sẽ bổ sung ở bước Executor)_

## Bảo mật

_(sẽ bổ sung ở bước rà soát)_

## Lộ trình

- [x] 1. Khởi tạo monorepo, docker-compose, README khung
- [ ] 2. Backend: models + CRUD flows + test
- [ ] 3. Executor + tools + sandbox, test với mock
- [ ] 4. WebSocket log stream
- [ ] 5. Frontend: Flows list + Flow Editor
- [ ] 6. Frontend: Run + History
- [ ] 7. Tích hợp GitHub tạo PR
- [ ] 8. Rà soát bảo mật, hoàn thiện README
