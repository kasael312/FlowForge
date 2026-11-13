# Rà soát bảo mật (bước 8)

Rà soát toàn bộ backend, frontend và cấu hình triển khai của FlowForge. Mỗi mục ghi mức độ, mô tả
ngắn và trạng thái. Mã đã sửa có test đi kèm trong `backend/tests/test_security.py` và
`backend/tests/test_sandbox_ext.py`.

## Đã sửa

| Mức | Vấn đề | Cách sửa |
|---|---|---|
| Nghiêm trọng | MCP server kiểu `command` (stdio) cho phép bất kỳ user nào chạy lệnh tuỳ ý trên host backend (backend có Docker socket). | Chỉ **admin** mới tạo/sửa MCP stdio (`MCP_STDIO_ADMIN_ONLY=true`); user thường chỉ dùng URL HTTP tới host công khai. |
| Nghiêm trọng | `pattern` của trigger `file_watch` không được kiểm tra: `../../*` hoặc `**` đọc và di chuyển file ngoài thư mục theo dõi (kể cả DB). | Pattern chỉ được là glob tên file (không `/`, `..`, `**`); `scan_folder` chỉ nhận file nằm trực tiếp trong thư mục, bỏ symlink, kiểm tra `resolve()`. |
| Cao | `query_sql` nhận URL `sqlite:///./data/flowforge.db` nên đọc được DB của ứng dụng. | Chỉ chấp nhận dialect mạng (postgresql, mysql, mariadb, mssql, oracle, clickhouse, snowflake…); từ chối URL file. |
| Cao | Flow tự chọn `network: bridge` bất kể cấu hình server; sandbox có egress mở tới host, metadata, sandbox khác. | `SANDBOX_MAX_NETWORK` là trần (`none < allowlist < bridge`, mặc định `allowlist`); `allowed_hosts` không nhận `*`, IP, `localhost`, `*.internal`. |
| Cao | SSRF từ backend: `http_request`, `fetch_url`/`scrape`, `webhook_call`, `base_url` của model, URL MCP chỉ chặn vài tên như `localhost`; redirect không được kiểm tra. | Bộ lọc chung `app/net.py`: resolve DNS và từ chối mọi địa chỉ loopback/private/link-local/metadata; `guarded_client` kiểm tra lại từng request kể cả sau redirect; `base_url`/URL MCP của user thường phải công khai (ngoại lệ cấu hình `TRUSTED_INTERNAL_URLS`, mặc định Ollama local). |
| Trung bình | Proxy allowlist là open proxy trên mạng bridge, không kiểm tra IP đích (DNS rebinding), mọi port. | Proxy yêu cầu `Proxy-Authorization` với token riêng từng run; resolve tên và chặn địa chỉ private; chỉ port 80/443/8080/8443; lỗi port → 400. |
| Trung bình | Thu thập artifact tin vào output `find` phân tách bằng tab/newline: tên file chứa `\n` ghi ra ngoài thư mục run, cỡ khai báo qua mặt giới hạn. | `find -printf '%p\0%s\0'`, kiểm tra đường dẫn đích nằm trong thư mục run, kiểm tra lại kích thước sau khi đọc. |
| Trung bình | Không thu hồi được phiên: logout chỉ xoá cookie, đổi mật khẩu không vô hiệu token cũ (refresh 7 ngày). | `users.token_version` ghi vào JWT; logout và đổi mật khẩu tăng version → mọi token cũ bị từ chối ("session revoked"). |
| Trung bình | Không giới hạn đăng nhập sai; đăng ký mở hoàn toàn. | Giới hạn theo (IP, email): `LOGIN_MAX_FAILURES` trong `LOGIN_WINDOW_SECONDS` → 429; `REGISTRATION=open \| first_user \| closed`. Tài khoản đầu tiên là admin. |
| Trung bình | CSRF chỉ dựa vào `SameSite=Lax`. | Middleware từ chối request POST/PUT/PATCH/DELETE có `Origin` không thuộc `CORS_ORIGINS` hoặc chính origin của API (trừ `/hooks/*`); cookie refresh `SameSite=Strict`; `CORS_ORIGINS=*` bị bỏ qua. |
| Trung bình | File của skill import từ tài liệu mang `content_type` tuỳ ý và được phục vụ `inline` → XSS trên origin API. | Content type suy từ phần mở rộng (bảng an toàn), tải về dạng `attachment` kèm `nosniff`. |
| Trung bình | Bảng giá dùng chung nhưng user nào cũng sửa được. | `PUT /pricing` chỉ admin. |
| Trung bình | Jinja `range(10**9)` / regex người dùng có thể chặn event loop. | `range` trong template giới hạn 10 000 phần tử. (Regex trong condition vẫn chạy đồng bộ, xem phần "Chấp nhận".) |
| Thấp | Secret webhook trả về ở mọi lần liệt kê trigger. | Chỉ trả secret khi tạo hoặc xoay; danh sách trả `null`, UI ghi chú. |
| Thấp | Webhook/trigger bỏ qua validate input schema. | `fire()` chạy `coerce_inputs` theo schema của flow. |
| Thấp | Không giới hạn kích thước body. | Middleware từ chối `Content-Length` > 110 MB (413); upload vốn giới hạn 100 MB. |
| Thấp | Link chia sẻ lộ header/deliver_args có token người dùng gõ vào. | View công khai che giá trị của khoá trông như credential (`auth`, `token`, `secret`, `key`, `password`…). |
| Thấp | Thiếu header bảo mật. | Backend: `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Cache-Control: no-store`. Frontend nginx: CSP, `nosniff`, `DENY`, `Referrer-Policy`, `Permissions-Policy`. |
| Thấp | Mặc định dev không an toàn không được cảnh báo. | Cảnh báo lúc khởi động khi `JWT_SECRET` trống hoặc `REGISTRATION=open`; `.env.example` ghi rõ. `SHELL_WHITELIST` mẫu bỏ `go,cargo` (không có trong image). |

## Chấp nhận / khuyến nghị khi triển khai

- **Docker socket**: backend cần `/var/run/docker.sock` để chạy sandbox, tương đương root trên host.
  Chạy FlowForge trên máy tin cậy, cân nhắc `docker-socket-proxy` chỉ mở `containers`, `exec`,
  `networks`, và không cho người lạ đăng ký (`REGISTRATION=first_user|closed`).
- **Cổng**: `docker-compose.yml` mở `8000` và `5173` trên mọi interface; đặt sau reverse proxy có TLS
  (`COOKIE_SECURE=true`, `CORS_ORIGINS` đúng origin) hoặc đổi thành `127.0.0.1:8000:8000`.
- **Secret**: đặt `JWT_SECRET` và `APP_SECRET` mạnh, cố định; đổi `APP_SECRET` làm mọi key đã lưu
  không giải mã được.
- **Regex trong condition**: `matches("…")` do người dùng viết chạy đồng bộ; pattern độc hại có thể
  làm chậm executor của chính họ (ReDoS). Giới hạn độ dài output và `MAX_STEPS_PER_RUN` hạn chế tác động.
- **Liệt kê email**: `POST /auth/register` trả 409 khi email đã tồn tại; chấp nhận để UX rõ ràng
  (đăng nhập vẫn trả lỗi chung).
- **Lỗi upstream** (MCP, provider) được trả về dưới dạng tên exception + thông điệp ngắn; không chứa key.
- **Dữ liệu người dùng nhập vào prompt** đi thẳng tới model; rule `no-secrets` và mã hoá credential
  giảm rủi ro nhưng không thay thế việc không dán secret vào prompt.

## Những gì đã đúng từ trước (giữ nguyên)

- Kiểm tra quyền sở hữu nhất quán cho mọi tài nguyên; artifact/step chỉ qua run của chính mình;
  export không kèm key; skill/model/key của người khác không mượn được qua import/clone.
- JWT HS256 cố định thuật toán, có `exp`, cookie httpOnly; bcrypt; refresh cookie giới hạn path.
- Credential mã hoá Fernet, API chỉ trả `last4`; MCP env/header chỉ lộ tên khoá.
- Webhook: HMAC `compare_digest`, body ≤ 1 MB, 404/401 tách bạch.
- Sandbox: `docker exec` argv, `--cap-drop ALL`, `no-new-privileges`, giới hạn RAM/CPU/PID/thời gian,
  user không root, network mặc định `none`, whitelist lệnh + chặn ký tự shell, đường dẫn trong `/repo`.
- Condition evaluator là AST whitelist không `eval`; Jinja `SandboxedEnvironment`; zip skill kiểm tra
  đường dẫn/kích thước; tên file upload được làm sạch; WebSocket xác thực trước khi gửi dữ liệu.
