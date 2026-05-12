"""account_manager - 多平台账号管理后台"""
import os
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from core.db import init_db
from core.registry import load_all
from api.accounts import router as accounts_router
from api.tasks import router as tasks_router
from api.platforms import router as platforms_router
from api.proxies import router as proxies_router
from api.config import router as config_router
from api.actions import router as actions_router
from api.integrations import router as integrations_router
from api.auth import router as auth_router
from api.contribution import router as contribution_router
from api.chatgpt import router as chatgpt_router

EXPECTED_CONDA_ENV = os.getenv("APP_CONDA_ENV", "any-auto-register")


def _env_enabled(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).lower() not in {"0", "false", "no", "off", ""}


def _detect_conda_env() -> str:
    conda_env = os.getenv("CONDA_DEFAULT_ENV")
    if conda_env:
        return conda_env

    prefix_parts = os.path.normpath(sys.prefix).split(os.sep)
    if "envs" in prefix_parts:
        idx = prefix_parts.index("envs")
        if idx + 1 < len(prefix_parts):
            return prefix_parts[idx + 1]
    return ""


def _print_runtime_info() -> None:
    current_env = _detect_conda_env()
    print(f"[Runtime] Python: {sys.executable}")
    print(f"[Runtime] Conda Env: {current_env or '未检测到'}")
    if EXPECTED_CONDA_ENV == "docker":
        return
    if current_env and current_env != EXPECTED_CONDA_ENV:
        print(
            f"[WARN] 当前环境为 '{current_env}'，推荐使用 '{EXPECTED_CONDA_ENV}' 启动，"
            "否则 Turnstile Solver 可能因依赖缺失而无法启动。"
        )
    elif not current_env:
        print(
            f"[WARN] 未检测到 conda 环境，推荐使用 '{EXPECTED_CONDA_ENV}' 启动，"
            "否则 Turnstile Solver 可能因依赖缺失而无法启动。"
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _print_runtime_info()
    init_db()
    load_all()
    print("[OK] 数据库初始化完成")
    from core.registry import list_platforms
    print(f"[OK] 已加载平台: {[p['name'] for p in list_platforms()]}")
    from core.scheduler import scheduler
    scheduler.start()
    if _env_enabled("APP_AUTOSTART_SOLVER"):
        from services.solver_manager import start_async
        start_async()
    else:
        print("[Solver] 后端启动不自动拉起；需要时可在设置页手动启动")
    # WhatsApp Relay 后台进程（GoPay OTP 全自动接收）
    if _env_enabled("APP_AUTOSTART_WA_RELAY"):
        from services.wa_relay_manager import start_async as _wa_start_async
        _wa_start_async(login_mode="qr")
    else:
        print("[WA-Relay] 后端启动不自动拉起；需要时可在设置页手动启动")
    yield
    from core.scheduler import scheduler as _scheduler
    _scheduler.stop()
    from services.solver_manager import stop
    stop()
    from services.wa_relay_manager import stop as _wa_stop
    _wa_stop()


app = FastAPI(title="Account Manager", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/auth/") or not path.startswith("/api/"):
        return await call_next(request)
    from core.config_store import config_store as _cs
    if not _cs.get("auth_password_hash", ""):
        return await call_next(request)
    auth_header = request.headers.get("Authorization", "")
    token = ""
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    else:
        # SSE / EventSource 不支持自定义 header，允许通过 query string ?token=xxx 鉴权
        token = (request.query_params.get("token") or "").strip()
    if not token:
        return JSONResponse({"detail": "未认证，请先登录"}, status_code=401)
    try:
        from api.auth import verify_token
        verify_token(token)
    except HTTPException as e:
        return JSONResponse({"detail": e.detail}, status_code=e.status_code)
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(accounts_router, prefix="/api")
app.include_router(tasks_router, prefix="/api")
app.include_router(platforms_router, prefix="/api")
app.include_router(proxies_router, prefix="/api")
app.include_router(config_router, prefix="/api")
app.include_router(actions_router, prefix="/api")
app.include_router(integrations_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(chatgpt_router, prefix="/api")
app.include_router(contribution_router)


@app.get("/api/solver/status")
def solver_status():
    from services.solver_manager import is_running
    return {"running": is_running()}


@app.post("/api/solver/restart")
def solver_restart():
    from services.solver_manager import stop, start_async
    stop()
    start_async()
    return {"message": "重启中"}


@app.get("/api/wa_relay/status")
def wa_relay_status():
    """WhatsApp Relay 状态：进程是否运行、是否登录、QR 码、配对码、最近 OTP 时间"""
    from services.wa_relay_manager import get_status
    return get_status()


@app.post("/api/wa_relay/restart")
def wa_relay_restart(payload: dict = None):
    """重启 Relay。可指定 login_mode=qr|pairing 和 pairing_phone"""
    from services.wa_relay_manager import restart
    payload = payload or {}
    mode = str(payload.get("login_mode") or "qr").lower()
    phone = str(payload.get("pairing_phone") or "").strip()
    restart(login_mode=mode, pairing_phone=phone)
    return {"message": "WhatsApp Relay 重启中"}


@app.post("/api/wa_relay/logout")
def wa_relay_logout():
    """注销 WhatsApp 登录（删除 session 后重启即出 QR）"""
    import shutil as _shutil
    from services.wa_relay_manager import _SESSION_DIR, restart
    try:
        if os.path.isdir(_SESSION_DIR):
            _shutil.rmtree(_SESSION_DIR, ignore_errors=True)
    except Exception:
        pass
    restart(login_mode="qr")
    return {"message": "已注销 WhatsApp，请重新扫码"}


@app.post("/api/gopay/unlink-all")
def gopay_unlink_all(payload: dict = None):
    """登录 GoPay 账号并解绑所有已链接 app（释放 GoPay 帐号供下个 ChatGPT 使用）。

    Body 可选: {"keep": ["AppName1"]}，留空则解绑全部。
    需提前在 Settings 配置 payment_gopay_phone + payment_gopay_pin。
    """
    from core.config_store import config_store as _cs
    from platforms.chatgpt.gopay_auto_register import GoPayRegistrar, GoPayAutoRegisterError

    payload = payload or {}
    keep = tuple(payload.get("keep") or [])

    phone_full = (_cs.get("payment_gopay_phone") or "").strip().lstrip("+").replace(" ", "")
    pin = (_cs.get("payment_gopay_pin") or "").strip()
    smsbower_key = (_cs.get("smsbower_api_key") or "").strip()

    if not phone_full or not pin:
        raise HTTPException(status_code=400, detail="请先在 Settings 配置 payment_gopay_phone 和 payment_gopay_pin")

    try:
        registrar = GoPayRegistrar(
            smsbower_api_key=smsbower_key or "DUMMY",
            proxy_url="",
        )
        access_token = registrar.login_existing_account(phone_full, pin)
        unlinked_count = registrar.unlink_all_apps(access_token, keep_app_names=keep)
        return {
            "ok": True,
            "unlinked": unlinked_count,
            "kept": list(keep),
            "message": f"已解绑 {unlinked_count} 个 app",
        }
    except GoPayAutoRegisterError as exc:
        raise HTTPException(status_code=400, detail=f"GoPay 解绑失败: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"内部错误: {exc}")


_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    app.mount("/assets", StaticFiles(directory=os.path.join(_static_dir, "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        return FileResponse(os.path.join(_static_dir, "index.html"))


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    reload_enabled = os.getenv("APP_RELOAD", "0").lower() in {"1", "true", "yes"}
    uvicorn.run("main:app", host=host, port=port, reload=reload_enabled)
