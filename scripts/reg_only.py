"""
reg_only.py — 只注册 ChatGPT 基础账号，不获取 OAuth Token

方案四 · 步骤一：
  先把账号注册好放着（只做邮箱验证 + create_account），
  不立即走 OAuth Codex 授权，从而避免"刚注册就立刻用高权限应用"的风控触发。
  注册好的账号存到 scripts/pending_accounts.json，1-2 天后再用 test_phone_required.py 测试。

用法：
  python scripts/reg_only.py --count 5 --proxy http://127.0.0.1:7897 --delay 300
  python scripts/reg_only.py --count 10  (使用 .env 里的 PROXY_URL)

参数：
  --count   N      注册账号数量，默认 1
  --proxy   URL    代理地址，不填则读 .env PROXY_URL
  --delay   秒     每个账号注册完成后的等待秒数（防频率触发），默认 180
  --output  文件   输出 JSON 文件路径，默认 scripts/pending_accounts.json
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# —— 确保能 import 项目模块 ——
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env", override=False)


# ──────────────────────────────────────────────
# 邮箱服务适配器（复用 plugin.py 中的 CFWorker/LuckMail 逻辑）
# ──────────────────────────────────────────────

def _build_email_service(proxy: str):
    """构建邮箱服务，优先使用 CFWorker（custom_provider），否则降级到 LuckMail。"""
    cfworker_url = os.getenv("CFWORKER_API_URL", "").strip()
    cfworker_domain = os.getenv("CFWORKER_DOMAIN", "").strip()
    luckmail_key = os.getenv("LUCKMAIL_API_KEY", "").strip()

    if cfworker_url and cfworker_domain:
        from core.mailbox_cfworker import CFWorkerMailbox
        mailbox = CFWorkerMailbox(api_url=cfworker_url, domain=cfworker_domain, proxy=proxy)
    elif luckmail_key:
        from core.mailbox_luckmail import LuckMailMailbox
        mailbox = LuckMailMailbox(api_key=luckmail_key, proxy=proxy)
    else:
        # 最后兜底：tempmail_lol（不需要配置）
        from core.base_mailbox import TempMailLolMailbox
        mailbox = TempMailLolMailbox(proxy=proxy)

    return mailbox


def _build_generic_email_service(mailbox):
    """把 mailbox 对象包装成 RefreshTokenRegistrationEngine 所需的 email_service 接口。"""

    class _EmailService:
        service_type = type("ST", (), {"value": "custom_provider"})()

        def __init__(self):
            self._acct = None
            self._before_ids = set()

        def create_email(self, config=None):
            self._acct = mailbox.get_email()
            get_current_ids = getattr(mailbox, "get_current_ids", None)
            self._before_ids = set(get_current_ids(self._acct) or []) if callable(get_current_ids) else set()
            email = str(getattr(self._acct, "email", "") or "").strip()
            if not email:
                raise RuntimeError("邮箱服务返回空邮箱地址")
            return {"email": email, "service_id": getattr(self._acct, "account_id", email), "token": ""}

        def get_verification_code(self, email=None, email_id=None, timeout=120,
                                   pattern=None, otp_sent_at=None, exclude_codes=None):
            if not self._acct:
                raise RuntimeError("邮箱账户尚未创建")
            return mailbox.wait_for_code(
                self._acct,
                keyword="",
                timeout=timeout,
                before_ids=self._before_ids,
                otp_sent_at=otp_sent_at,
                exclude_codes=exclude_codes,
            )

        def update_status(self, success, error=None):
            pass

        @property
        def status(self):
            return None

    return _EmailService()


# ──────────────────────────────────────────────
# 核心：只做基础注册，跳过 OAuth Token 阶段
# ──────────────────────────────────────────────

def register_consumer_only(proxy: str, log_fn=print) -> dict:
    """
    执行"只注册，不取 Token"流程：
      1. IP 地理检测 + 指纹初始化
      2. 创建邮箱
      3. chatgpt.com 消费者注册（邮箱 OTP + create_account）
      跳过 → OAuth Codex 授权 / Token 交换

    返回 dict，包含 email / password / registered_at / status / error
    """
    from platforms.chatgpt.refresh_token_registration_engine import (
        RefreshTokenRegistrationEngine,
        RegistrationResult,
    )
    from platforms.chatgpt.chatgpt_client import ChatGPTClient
    from platforms.chatgpt.utils import normalize_flow_url
    from platforms.chatgpt.constants import generate_random_user_info

    # 构建邮箱服务
    mailbox = _build_email_service(proxy)
    email_service = _build_generic_email_service(mailbox)

    logs = []

    def _log(msg):
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        logs.append(line)
        log_fn(line)

    # ── 实例化引擎（只用它的辅助方法，不调用 run()） ──
    engine = RefreshTokenRegistrationEngine(
        email_service=email_service,
        proxy_url=proxy,
        callback_logger=_log,
        browser_mode="headless",
        extra_config={},
    )

    result_dict = {
        "email": "",
        "password": "",
        "registered_at": "",
        "status": "pending",   # pending = 注册好但未取 Token
        "error": "",
        "logs": logs,
    }

    try:
        # ── 步骤 1：检查 IP 位置 ──
        _log("1. 检查 IP 地理位置...")
        ip_ok, location = engine._check_ip_location()
        if not ip_ok:
            result_dict["status"] = "failed"
            result_dict["error"] = f"IP 检查失败: {location}"
            return result_dict
        _log(f"IP 位置: {location}")
        if location:
            engine._reinit_stealth_components(geo_code=location)
            _log(f"Session 指纹已初始化: {engine._session_fp}")

        # ── 步骤 2：创建邮箱 ──
        _log("2. 创建邮箱...")
        if not engine._create_email():
            result_dict["status"] = "failed"
            result_dict["error"] = "创建邮箱失败"
            return result_dict
        result_dict["email"] = engine.email

        # ── 步骤 3：消费者注册（走 chatgpt.com 流程） ──
        _log("3. 普通 ChatGPT 网页注册...")
        reg_result = RegistrationResult(success=False, logs=logs)
        ok, err = engine._create_consumer_chatgpt_basic_account(reg_result)
        if not ok:
            # 尝试看 post_otp_page_type，如果已到 about_you / add_phone 说明账号实际已建立
            page_type = engine._post_otp_page_type.lower()
            if page_type in {"about_you", "add_phone", "consent", "external_url", "oauth_callback"}:
                _log(f"注册停在 {page_type}，账号已创建，跳过 OAuth，标记为 pending")
                result_dict["password"] = engine.password or reg_result.password or ""
                result_dict["status"] = "pending"
                result_dict["registered_at"] = datetime.now(timezone.utc).isoformat()
                return result_dict
            result_dict["status"] = "failed"
            result_dict["error"] = err or "consumer_chatgpt_registration_failed"
            return result_dict

        # 注册成功，记录密码
        password = engine.password or reg_result.password or ""
        result_dict["password"] = password
        result_dict["status"] = "pending"
        result_dict["registered_at"] = datetime.now(timezone.utc).isoformat()
        _log(f"✅ 基础账号注册成功，Email={engine.email}，跳过 OAuth 等待延后测试")

    except Exception as exc:
        import traceback
        result_dict["status"] = "failed"
        result_dict["error"] = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        _log(f"注册异常: {exc}")

    return result_dict


# ──────────────────────────────────────────────
# 输出管理
# ──────────────────────────────────────────────

def load_pending(output_path: Path) -> list:
    if output_path.exists():
        try:
            return json.loads(output_path.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_pending(accounts: list, output_path: Path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(accounts, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ──────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="只注册 ChatGPT 基础账号（不取 OAuth Token），存到 pending_accounts.json"
    )
    parser.add_argument("--count",  type=int, default=1,    help="注册账号数量")
    parser.add_argument("--proxy",  type=str, default="",   help="代理地址，留空读 .env PROXY_URL")
    parser.add_argument("--delay",  type=int, default=180,  help="每个账号注册后的等待秒数")
    parser.add_argument(
        "--output", type=str,
        default=str(_ROOT / "scripts" / "pending_accounts.json"),
        help="输出 JSON 文件路径",
    )
    args = parser.parse_args()

    proxy = args.proxy.strip() or os.getenv("PROXY_URL", "").strip() or None
    output_path = Path(args.output)

    print(f"[配置] 代理: {proxy or '无'}")
    print(f"[配置] 计划注册: {args.count} 个账号")
    print(f"[配置] 账号间延迟: {args.delay}s")
    print(f"[配置] 输出文件: {output_path}")
    print()

    accounts = load_pending(output_path)
    success_count = 0
    fail_count = 0

    for i in range(1, args.count + 1):
        print(f"{'='*60}")
        print(f"[{i}/{args.count}] 开始注册第 {i} 个账号...")
        print(f"{'='*60}")

        record = register_consumer_only(proxy=proxy)
        # 去掉 logs 字段（太长），单独保存摘要
        record_summary = {k: v for k, v in record.items() if k != "logs"}
        accounts.append(record_summary)
        save_pending(accounts, output_path)

        if record["status"] == "pending":
            success_count += 1
            print(f"\n✅ 第 {i} 个账号注册成功: {record['email']}")
        else:
            fail_count += 1
            print(f"\n❌ 第 {i} 个账号注册失败: {record.get('error','')[:120]}")

        if i < args.count:
            print(f"\n⏳ 等待 {args.delay}s 后继续下一个账号...")
            time.sleep(args.delay)

    print(f"\n{'='*60}")
    print(f"全部完成：成功 {success_count} 个，失败 {fail_count} 个")
    print(f"账号已保存到: {output_path}")
    print(f"请等待 1-2 天后运行 test_phone_required.py 测试是否需要手机号")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
