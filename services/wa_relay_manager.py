"""WhatsApp Relay 进程管理 - 后端启动时自动拉起，全自动捕获 GoPay OTP

工作原理：
- 通过 Node.js 启动 webui/whatsapp_relay/index.js（Baileys WhatsApp 多端协议）
- 监听 WhatsApp 消息，正则提取 6 位 OTP，写入 wa-otp.txt
- card.py / gopay.py 通过 payment_gopay_otp_file 读取该文件
- 首次需扫码登录（QR / pairing），session 持久化到 .wa-session 目录
"""
from __future__ import annotations

import os
import sys
import time
import json
import shutil
import threading
import subprocess
from typing import Optional

_proc: Optional[subprocess.Popen] = None
_log_file = None
_lock = threading.Lock()
_stream_restart_attempts = 0

# 路径常量
_DEFAULT_RELAY_SRC_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "webui", "whatsapp_relay")
)
_RUNTIME_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "runtime", "wa_relay")
)
_STATE_FILE = os.path.join(_RUNTIME_DIR, "wa-state.json")
_OTP_FILE = os.path.join(_RUNTIME_DIR, "wa-otp.txt")
_SESSION_DIR = os.path.join(_RUNTIME_DIR, ".wa-session")
_LOG_FILE = os.path.join(_RUNTIME_DIR, "wa-relay.log")


def _wa_relay_enabled() -> bool:
    """通过环境变量控制开关，默认开启"""
    return os.getenv("APP_ENABLE_WA_RELAY", "1").lower() not in {"0", "false", "no"}


def _node_executable() -> str:
    """查找 node 可执行文件"""
    configured = os.getenv("WA_NODE_EXE", "").strip() or os.getenv("NODE_EXE", "").strip()
    if configured:
        configured = os.path.normpath(os.path.expandvars(os.path.expanduser(configured)))
        if os.path.isfile(configured):
            return configured
    node = shutil.which("node")
    if node:
        return node
    # Windows 常见路径
    for p in (
        r"C:\Program Files\nodejs\node.exe",
        r"C:\Program Files (x86)\nodejs\node.exe",
        r"D:\Nodejs\node.exe",
        r"E:\Nodejs\node.exe",
        r"F:\Nodejs\node.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "nodejs", "node.exe"),
    ):
        if os.path.exists(p):
            return p
    return ""


def _ensure_runtime_dir():
    os.makedirs(_RUNTIME_DIR, exist_ok=True)


def _relay_src_dir() -> str:
    configured = os.getenv("WA_RELAY_SRC_DIR", "").strip()
    if not configured:
        try:
            from core.config_store import config_store
            configured = str(config_store.get("wa_relay_src_dir", "") or "").strip()
        except Exception:
            configured = ""
    return os.path.normpath(os.path.expandvars(os.path.expanduser(configured or _DEFAULT_RELAY_SRC_DIR)))


def _relay_proxy_url() -> str:
    configured = os.getenv("WA_RELAY_PROXY_URL", "").strip() or os.getenv("WA_PROXY_URL", "").strip()
    if not configured:
        try:
            from core.config_store import config_store
            configured = str(config_store.get("wa_relay_proxy_url", "") or "").strip()
        except Exception:
            configured = ""
    return configured


def _ensure_node_modules() -> bool:
    """确认 relay 源码下 node_modules 已安装；缺失则自动 npm install"""
    relay_src_dir = _relay_src_dir()
    nm = os.path.join(relay_src_dir, "node_modules")
    if os.path.isdir(nm):
        return True
    npm = shutil.which("npm")
    if not npm:
        # Windows 下 npm 是 .cmd
        for p in (
            r"C:\Program Files\nodejs\npm.cmd",
            r"C:\Program Files (x86)\nodejs\npm.cmd",
            r"D:\Nodejs\npm.cmd",
            r"E:\Nodejs\npm.cmd",
            r"F:\Nodejs\npm.cmd",
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "nodejs", "npm.cmd"),
        ):
            if os.path.exists(p):
                npm = p
                break
    if not npm:
        print("[WA-Relay] 找不到 npm，无法自动安装依赖")
        return False
    print("[WA-Relay] 首次启动，正在安装 Node.js 依赖（约 100MB，1-3 分钟）...")
    try:
        result = subprocess.run(
            [npm, "install", "--no-audit", "--no-fund", "--loglevel=error"],
            cwd=relay_src_dir,
            timeout=300,
            capture_output=True,
            text=True,
            shell=False,
        )
        if result.returncode != 0:
            print(f"[WA-Relay] npm install 失败：{result.stderr[:300]}")
            return False
        print("[WA-Relay] 依赖安装完成")
        return True
    except Exception as exc:
        print(f"[WA-Relay] npm install 异常：{exc}")
        return False


def is_running() -> bool:
    """进程是否存活"""
    return _proc is not None and _proc.poll() is None


def _monitor_process(proc: subprocess.Popen, login_mode: str, pairing_phone: str):
    global _proc, _log_file, _stream_restart_attempts
    code = proc.wait()
    with _lock:
        if _proc is proc:
            _proc = None
            if _log_file:
                try:
                    _log_file.close()
                except Exception:
                    pass
                _log_file = None
    if code == 12 and _stream_restart_attempts < 3:
        _stream_restart_attempts += 1
        time.sleep(1.5)
        print(f"[WA-Relay] Stream restart required，自动重启 Relay ({_stream_restart_attempts}/3)")
        start(login_mode=login_mode, pairing_phone=pairing_phone)


def _load_state() -> dict:
    try:
        if not os.path.exists(_STATE_FILE):
            return {}
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _state_is_logged_in(state: dict) -> bool:
    status = str(state.get("status") or state.get("connection") or state.get("state") or "").strip().lower()
    if status in {"connected", "open", "ready", "logged_in", "login", "authenticated"}:
        return True
    if state.get("logged_in") is True or state.get("is_logged_in") is True or state.get("authenticated") is True:
        return True
    user = state.get("user") or state.get("me") or state.get("account")
    return isinstance(user, dict) and bool(user)


def is_logged_in() -> bool:
    """检查 WhatsApp 是否已登录（state 文件中 status=connected）"""
    return _state_is_logged_in(_load_state())


def get_status() -> dict:
    """供 API 查询的状态信息"""
    node_path = _node_executable()
    info = {
        "enabled": _wa_relay_enabled(),
        "process_running": is_running(),
        "logged_in": is_logged_in(),
        "otp_file": _OTP_FILE,
        "log_file": _LOG_FILE,
        "state_file": _STATE_FILE,
        "relay_src_dir": _relay_src_dir(),
        "relay_src_exists": os.path.isdir(_relay_src_dir()),
        "node_available": bool(node_path),
        "node_path": node_path,
        "proxy_url": _relay_proxy_url(),
    }
    # 尝试附加 state 文件中的 QR/pairing 信息（字段名匹配 relay index.js 实际输出）
    try:
        state = _load_state()
        if state:
            info["status"] = state.get("status", "")
            info["logged_in"] = _state_is_logged_in(state)
            qr_data_url = (
                state.get("qr_data_url")
                or state.get("qrDataUrl")
                or state.get("qr_code_data_url")
                or state.get("qrCodeDataUrl")
                or state.get("qr_image")
                or state.get("qrImage")
            )
            qr_text = state.get("qr") or state.get("qr_text") or state.get("qrText") or state.get("qr_code") or state.get("qrCode")
            pairing_code = state.get("code") or state.get("pairing_code") or state.get("pairingCode") or state.get("pairCode")
            if qr_data_url:
                info["qr_data_url"] = qr_data_url
            if qr_text:
                info["qr_text"] = qr_text
            if pairing_code:
                info["pairing_code"] = pairing_code
            if state.get("latest"):
                info["latest_otp_time"] = state.get("latest", {}).get("ts")
                info["latest_otp"] = state.get("latest", {}).get("otp", "")
            if state.get("error"):
                info["error"] = state.get("error")
    except Exception:
        pass
    return info


def start(login_mode: str = "qr", pairing_phone: str = ""):
    """启动 relay 进程
    
    Args:
        login_mode: "qr" 或 "pairing"
        pairing_phone: pairing 模式时的手机号（纯数字含国家码，如 8615870862693）
    """
    global _proc, _log_file
    with _lock:
        if not _wa_relay_enabled():
            print("[WA-Relay] 已禁用 (APP_ENABLE_WA_RELAY=0)，跳过自动启动")
            return
        if is_running():
            print("[WA-Relay] 已在运行")
            return
        relay_src_dir = _relay_src_dir()
        node = _node_executable()
        if not node:
            print("[WA-Relay] 找不到 Node.js，请安装 Node.js v18+")
            return
        if not os.path.isdir(relay_src_dir):
            print(f"[WA-Relay] 源码目录不存在: {relay_src_dir}")
            return
        if not _ensure_node_modules():
            return
        _ensure_runtime_dir()

        env = os.environ.copy()
        node_dir = os.path.dirname(node)
        if node_dir:
            env["PATH"] = node_dir + os.pathsep + env.get("PATH", "")
        env["WA_ENGINE"] = os.getenv("WA_ENGINE", "baileys")
        env["WA_LOGIN_MODE"] = login_mode
        if pairing_phone:
            env["WA_PAIRING_PHONE"] = pairing_phone
        env["WA_STATE_FILE"] = _STATE_FILE
        env["WA_OTP_FILE"] = _OTP_FILE
        env["WA_SESSION_DIR"] = _SESSION_DIR
        env["WA_HEADLESS"] = "1"
        relay_proxy = _relay_proxy_url()
        if relay_proxy:
            env["WA_PROXY_URL"] = relay_proxy
            env["HTTPS_PROXY"] = relay_proxy
            env["HTTP_PROXY"] = relay_proxy

        _log_file = open(_LOG_FILE, "a", encoding="utf-8")
        _log_file.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} 启动 ===\n")
        _log_file.flush()

        creationflags = 0
        if sys.platform == "win32":
            # 隐藏控制台窗口
            creationflags = 0x08000000  # CREATE_NO_WINDOW

        try:
            _proc = subprocess.Popen(
                [node, "index.js"],
                cwd=relay_src_dir,
                env=env,
                stdout=_log_file,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
            # 等待 5 秒看进程是否仍在运行
            for _ in range(5):
                time.sleep(1)
                if _proc.poll() is not None:
                    print(
                        f"[WA-Relay] 启动失败，退出码={_proc.returncode}，"
                        f"详细日志: {_LOG_FILE}"
                    )
                    _proc = None
                    if _log_file:
                        _log_file.close()
                        _log_file = None
                    return
            threading.Thread(
                target=_monitor_process,
                args=(_proc, login_mode, pairing_phone),
                daemon=True,
            ).start()
            print(
                f"[WA-Relay] 已启动 PID={_proc.pid}, "
                f"OTP 文件={_OTP_FILE}"
            )
            if not is_logged_in():
                print(
                    "[WA-Relay] ⚠️ WhatsApp 未登录，请打开前端 → 设置 → "
                    "WhatsApp Relay 扫描 QR 码或使用配对码"
                )
        except Exception as exc:
            print(f"[WA-Relay] 启动异常：{exc}")
            _proc = None
            if _log_file:
                _log_file.close()
                _log_file = None


def stop():
    global _proc, _log_file
    with _lock:
        if _proc and _proc.poll() is None:
            try:
                _proc.terminate()
                _proc.wait(timeout=5)
            except Exception:
                try:
                    _proc.kill()
                except Exception:
                    pass
            print("[WA-Relay] 已停止")
        _proc = None
        if _log_file:
            try:
                _log_file.close()
            except Exception:
                pass
            _log_file = None


def restart(login_mode: str = "qr", pairing_phone: str = ""):
    """重启 relay（用于切换登录模式或刷新 QR）"""
    global _stream_restart_attempts
    _stream_restart_attempts = 0
    stop()
    time.sleep(0.5)
    start(login_mode=login_mode, pairing_phone=pairing_phone)


def start_async(login_mode: str = "qr", pairing_phone: str = ""):
    """在后台线程启动，不阻塞主进程"""
    t = threading.Thread(
        target=start,
        kwargs={"login_mode": login_mode, "pairing_phone": pairing_phone},
        daemon=True,
    )
    t.start()


def get_otp_file_path() -> str:
    """供其他模块查询 OTP 文件路径"""
    return _OTP_FILE
