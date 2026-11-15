"""Ứng dụng FastAPI: API cho giao diện web + phục vụ bản build React (web/dist).

Hai tiến trình (triển khai thật – docs/07):
  • Nội bộ (nhân viên):  uvicorn api.main:app --host <IP mạng nội bộ> --port 8000        – KHÔNG mở ra Internet
  • Công khai (thai phụ):  uvicorn api.public_main:app --host 127.0.0.1 --port 8001     – chỉ /form và /api/public/*,
    đặt sau reverse proxy HTTPS; chỉ cổng này được mở ra Internet (đường dẫn trong SMS).
Chạy thử trên một máy:  .venv\\Scripts\\python.exe -m uvicorn api.main:app --port 8000   (gồm cả trang form)
Giao diện phát triển:   cd web && npm run dev   (Vite proxy /api → cổng 8000)
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from contextlib import asynccontextmanager

import bcrypt
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from api.routers import (
    alerts,
    appointments,
    auth,
    config,
    form_schedule,
    noshow,
    overview,
    patients,
    profile,
    public_form,
    quality,
)
from core.config import ROOT, is_production, load_env, load_rules
from core.db import init_db
from core.security import MissingSecretKey, MissingUsersFile, load_users, users_file

WEB_DIST = ROOT / "web" / "dist"
DEMO_PASSWORDS = ("nurse123", "doctor123", "admin123")  # mật khẩu mẫu trong config/users.yaml
MAX_BODY_BYTES = 20 * 1024 * 1024  # file HIS / danh sách đến lớn nhất hợp lý
MAX_PUBLIC_BODY_BYTES = 64 * 1024  # form thai phụ: vài chục câu + 2000 ký tự tự viết


def production_problems() -> list[str]:
    """Điều kiện tối thiểu để chạy APP_ENV=production. Rỗng = được phép khởi động."""
    problems = []
    if (os.environ.get("LLM_PROVIDER") or "").strip().lower() == "openai":
        problems.append("LLM_PROVIDER=openai gửi dữ liệu ra ngoài – chỉ dùng với dữ liệu giả")
    if os.environ.get("APP_COOKIE_SECURE") != "1":
        problems.append("APP_COOKIE_SECURE phải = 1 (chạy sau HTTPS)")
    for u in load_users():
        h = u.get("password_hash", "").encode()
        if any(bcrypt.checkpw(p.encode(), h) for p in DEMO_PASSWORDS):
            problems.append(f"tài khoản '{u.get('username')}' còn dùng mật khẩu demo")
    return problems


def bootstrap() -> None:
    load_env()
    if not users_file().exists():
        logging.getLogger(__name__).error(
            "Chưa có %s – đăng nhập sẽ không dùng được. Chạy: python scripts/make_key.py", users_file())
    if is_production() and (problems := production_problems()):
        raise RuntimeError("Không khởi động chế độ production: " + "; ".join(problems))
    init_db()
    from core.retention import purge_all

    purge_all(load_rules())


@asynccontextmanager
async def lifespan(app: FastAPI):
    from core import jobs

    bootstrap()
    run_jobs = getattr(app.state, "run_jobs", True)
    if run_jobs:  # chỉ tiến trình nội bộ: gom vàng theo giờ, gửi lại đỏ lỗi, xóa file quá hạn (APP_JOBS=0 để tắt)
        jobs.start()
    yield
    if run_jobs:
        jobs.stop()


# CSP: chỉ tải từ chính máy chủ; Mantine cần style nội tuyến; cấm nhúng trang vào iframe (chống bấm lừa nút "hiện số").
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; "
       "object-src 'none'")
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",  # token form nằm trên URL → không gửi kèm khi rời trang
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Content-Security-Policy": CSP,
}


async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
    path = request.url.path
    if path.startswith("/api/public/"):  # phần mở ra Internet: giới hạn tần suất theo IP
        from api.throttle import public_limiter

        wait = public_limiter.hit(request.client.host if request.client else "?")
        if wait:
            return JSONResponse({"detail": "Quá nhiều yêu cầu. Vui lòng thử lại sau ít phút."}, status_code=429,
                                headers={"Retry-After": str(wait)})
    # Giới hạn kích thước nội dung gửi lên (theo Content-Length) – chặn trước khi đọc vào bộ nhớ.
    if request.method in ("POST", "PUT", "PATCH"):
        if "content-length" not in request.headers and "chunked" in request.headers.get("transfer-encoding", ""):
            # Gửi kiểu "chunked" không có Content-Length sẽ lách được giới hạn dưới đây → bắt buộc có độ dài.
            return JSONResponse({"detail": "Thiếu Content-Length."}, status_code=411)
        limit = MAX_PUBLIC_BODY_BYTES if path.startswith("/api/public/") else MAX_BODY_BYTES
        try:
            size = int(request.headers.get("content-length", "0"))
        except ValueError:
            size = limit + 1
        if size > limit:
            return JSONResponse({"detail": "Nội dung gửi lên quá lớn."}, status_code=413)
    response = await call_next(request)
    docs = path in ("/api/docs", "/api/docs/oauth2-redirect")  # Swagger UI tải từ CDN
    for k, v in SECURITY_HEADERS.items():
        if not (docs and k == "Content-Security-Policy"):
            response.headers.setdefault(k, v)
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"  # dữ liệu bệnh nhân: không lưu bộ nhớ đệm trình duyệt/proxy
    if os.environ.get("APP_COOKIE_SECURE") == "1":  # chỉ khi chạy HTTPS
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response


def build_app(title: str, routers: Iterable[APIRouter], *, docs: bool, spa_paths: str | None,
              run_jobs: bool = True) -> FastAPI:
    """spa_paths: None = không phục vụ giao diện; "*" = mọi đường dẫn; "/form" = chỉ trang form thai phụ."""
    app = FastAPI(title=title, lifespan=lifespan, docs_url="/api/docs" if docs else None, redoc_url=None,
                  openapi_url="/api/openapi.json" if docs else None)
    app.state.run_jobs = run_jobs
    app.middleware("http")(security_headers)

    @app.exception_handler(MissingSecretKey)
    @app.exception_handler(MissingUsersFile)
    async def _not_set_up(_req: Request, exc: RuntimeError) -> JSONResponse:
        # Máy chưa cài đặt xong (clone mới chưa chạy make_key.py): 503 kèm cách sửa, không phải lỗi code (500).
        return JSONResponse({"detail": str(exc)}, status_code=503)

    for r in routers:
        app.include_router(r)

    if spa_paths and (WEB_DIST / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> FileResponse:
            if path == "api" or path.startswith("api/"):
                raise HTTPException(404, "Không có API này.")
            f = WEB_DIST / path
            if path and f.is_file() and WEB_DIST in f.resolve().parents and f.parent == WEB_DIST:
                return FileResponse(f)  # icon.svg… ở gốc dist
            if spa_paths == "*" or "/" + path == spa_paths:
                return FileResponse(WEB_DIST / "index.html")
            raise HTTPException(404, "Không có trang này.")

    return app


STAFF_ROUTERS = (auth.router, overview.router, alerts.router, appointments.router, form_schedule.router,
                 profile.router, patients.router, noshow.router, quality.router, config.router, public_form.router)


def create_app(serve_web: bool = True) -> FastAPI:
    """Ứng dụng nội bộ: toàn bộ API nhân viên + trang form công khai (để chạy thử trên một máy)."""
    load_env()
    return build_app("Trợ lý thai kỳ – API", (*STAFF_ROUTERS, public_form.public_router),
                     docs=not is_production(), spa_paths="*" if serve_web else None)


app = create_app()
