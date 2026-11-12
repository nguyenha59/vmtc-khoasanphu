"""Tiến trình CÔNG KHAI: chỉ trang form thai phụ (/form) và /api/public/*. Không có đăng nhập, không có API nhân viên.

Chỉ tiến trình này được mở ra Internet (qua reverse proxy HTTPS) để thai phụ mở đường dẫn trong SMS.
    .venv\\Scripts\\python.exe -m uvicorn api.public_main:app --host 127.0.0.1 --port 8001
APP_BASE_URL trong .env = địa chỉ HTTPS công khai trỏ tới tiến trình này.
"""

from __future__ import annotations

from api.main import build_app
from api.routers import public_form


def create_public_app():  # type: ignore[no-untyped-def]
    return build_app("Trợ lý thai kỳ – form thai phụ", (public_form.public_router,), docs=False, spa_paths="/form",
                     run_jobs=False)  # việc định kỳ chỉ chạy ở tiến trình nội bộ


app = create_public_app()
