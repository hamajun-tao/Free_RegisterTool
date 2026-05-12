"""
test_phone_required.py — 对已注册账号测试登录时是否要求绑定手机号

方案四 · 步骤二：
  读取 scripts/pending_accounts.json（reg_only.py 生成），
  对每个 pending 账号尝试走 OAuth Codex 授权流程，
  检测结果分三类：
    ✅ no_phone    — 授权成功，无需手机号，Token 已获取
    ⚠️  add_phone  — 触发了 add-phone 要求，等价于"需要手机验证"
    ❌ failed      — 登录失败（密码错/session 过期等）

  结果更新回 pending_accounts.json 的 status 字段，同时写 test_results.json 报告。

用法：
  python scripts/test_phone_required.py
  python scripts/test_phone_required.py --proxy http://127.0.0.1:7897
  python scripts/test_phone_required.py --input scripts/pending_accounts.json --limit 5
  python scripts/test_phone_required.py --delay 60  (每个账号测试间隔)
"""

import argparse
import json
import os
import sys
import time
import random
from datetime import datetime, timezone
from pathlib import Path

# —— 确保能 import 项目模块 ——
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env", override=False)


# ──────────────────────────────────────────────
# 核心：对一个已注册账号，尝试 OAuth 登录，检测是否触发 add-phone
# ──────────────────────────────────────────────

def probe_account_phone_requirement(email: str, password: str, proxy: str, log_fn=print) -> dict:
    """
    对单个账号执行 OAuth 登录流程（不实际做手机验证），
    只检测登录后会落到哪个页面。

    返回 dict：
        status:  "no_phone" | "add_phone" | "failed"
        page_type: 落地页面类型
        token:   如果 no_phone 则填 access_token（前20字符）
        error:   失败原因
    """
    from platforms.chatgpt.refresh_token_registration_engine import RefreshTokenRegistrationEngine
    from platforms.chatgpt.utils import normalize_flow_url

    logs = []

    def _log(msg):
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        logs.append(line)
        log_fn(line)

    result = {
        "email": email,
        "status": "failed",
        "page_type": "",
        "token_preview": "",
        "error": "",
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "logs": [],
    }

    # 构造一个最简邮箱服务（登录流程不需要发邮件，只用来满足接口）
    class _NoopEmailService:
        service_type = type("ST", (), {"value": "noop"})()

        def create_email(self, config=None):
            return {"email": email, "service_id": email, "token": ""}

        def get_verification_code(self, **kwargs):
            """登录时 OpenAI 会自动发 OTP 到注册邮箱，这里实际不会被调用（只在 add-phone 发现前停止）"""
            return None

        def update_status(self, success, error=None):
            pass

        @property
        def status(self):
            return None

    email_service = _NoopEmailService()

    engine = RefreshTokenRegistrationEngine(
        email_service=email_service,
        proxy_url=proxy,
        callback_logger=_log,
        browser_mode="headless",
        extra_config={},
    )
    engine.email = email
    engine.password = password

    try:
        # ── 步骤 1：IP 检测 + 指纹 ──
        ip_ok, location = engine._check_ip_location()
        if not ip_ok:
            result["error"] = f"IP 检测失败: {location}"
            result["logs"] = logs
            return result
        if location:
            engine._reinit_stealth_components(geo_code=location)

        # ── 步骤 2：初始化 OAuth 授权流程，拿到 Device ID + Sentinel ──
        did, sen_token = engine._prepare_authorize_flow("测试登录")
        if not did:
            result["error"] = "获取 Device ID 失败"
            result["logs"] = logs
            return result

        # ── 步骤 3：提交登录入口（邮箱） ──
        login_start = engine._submit_login_start(did, sen_token)
        if not login_start.success:
            result["error"] = f"登录入口提交失败: {login_start.error_message}"
            result["logs"] = logs
            return result

        page_type = (login_start.page_type or "").lower()
        _log(f"响应页面类型: {page_type}")

        # 如果直接进 OTP（免密模式），需要等邮件
        if page_type in {"email_otp_verification", "otp"}:
            _log("登录进入 OTP 邮箱验证页面，需要邮箱验证码才能继续")
            # 此处我们只检测，不真正等 OTP——等 OTP 后才会知道是否要 add_phone
            # 实际尝试等验证码（最多 60s 快速超时，避免长时间挂起）
            _log("尝试快速获取验证码（60s超时）...")
            try:
                email_id = email  # 不依赖 service_id
                engine.email_info = {"service_id": email_id}
                engine._otp_sent_at = time.time()

                # 重新构建一个能实际读邮件的服务（如果有 CFWorker）
                cfworker_url = os.getenv("CFWORKER_API_URL", "").strip()
                cfworker_domain = os.getenv("CFWORKER_DOMAIN", "").strip()
                luckmail_key = os.getenv("LUCKMAIL_API_KEY", "").strip()

                real_code = None
                if cfworker_url and cfworker_domain:
                    try:
                        from core.mailbox_cfworker import CFWorkerMailbox
                        mb = CFWorkerMailbox(api_url=cfworker_url, domain=cfworker_domain, proxy=proxy)
                        acct = mb.get_email_by_address(email)
                        if acct:
                            real_code = mb.wait_for_code(acct, keyword="", timeout=60, otp_sent_at=engine._otp_sent_at)
                    except Exception as e:
                        _log(f"CFWorker 获取验证码失败: {e}")

                if not real_code:
                    # 无法获取验证码，只能标记为"需要进一步人工确认"
                    result["status"] = "otp_needed"
                    result["page_type"] = page_type
                    result["error"] = "登录需要邮箱 OTP，但无法自动获取（邮箱不支持自动读取）"
                    result["logs"] = logs
                    return result

                # 校验 OTP
                engine._used_verification_codes.clear()
                engine._used_verification_codes.add(real_code)
                validated = engine._validate_verification_code(real_code)
                if not validated:
                    result["error"] = "OTP 校验失败"
                    result["logs"] = logs
                    return result

                page_type = (engine._post_otp_page_type or "").lower()
                _log(f"OTP 验证后页面类型: {page_type}")

            except Exception as e:
                result["error"] = f"OTP 处理异常: {e}"
                result["logs"] = logs
                return result

        elif page_type in {"login_password", "password"}:
            # 有密码模式：提交密码
            _log("进入密码页面，提交密码...")
            pwd_result = engine._submit_login_password()
            if not pwd_result.success:
                result["error"] = f"密码提交失败: {pwd_result.error_message}"
                result["logs"] = logs
                return result
            page_type = (pwd_result.page_type or "").lower()
            _log(f"密码提交后页面类型: {page_type}")

            # 密码后进入 OTP，等验证码
            if page_type in {"email_otp_verification", "otp"}:
                code = engine._get_verification_code()
                if not code:
                    result["error"] = "等待登录 OTP 超时"
                    result["logs"] = logs
                    return result
                validated = engine._validate_verification_code(code)
                if not validated:
                    result["error"] = "登录 OTP 校验失败"
                    result["logs"] = logs
                    return result
                page_type = (engine._post_otp_page_type or "").lower()
                _log(f"OTP 验证后页面类型: {page_type}")

        # ── 步骤 4：判断落地页面 ──
        result["page_type"] = page_type

        if page_type == "add_phone":
            _log("⚠️  检测到 add-phone！此账号登录时仍需手机号验证")
            result["status"] = "add_phone"
            result["logs"] = logs
            return result

        # 尝试完整走完 OAuth（不做手机验证，只看是否能拿到 token）
        if page_type in {
            "about_you", "consent", "workspace_selection", "organization_selection",
            "oauth_callback", "callback", "external_url", "workspace_ready",
            "sign_in_with_chatgpt_codex_consent",
        }:
            _log("登录后未触发 add-phone，尝试完整 OAuth 授权获取 Token...")
            from platforms.chatgpt.refresh_token_registration_engine import RegistrationResult

            reg_result = RegistrationResult(success=False, logs=logs)
            # _complete_post_otp_flow 会走 Workspace → OAuth 回调 → Token 交换
            ok = engine._complete_post_otp_flow(reg_result, exchange_token=True)
            if ok and (reg_result.access_token or reg_result.session_token):
                token_preview = (reg_result.access_token or reg_result.session_token)[:24] + "..."
                _log(f"✅ OAuth 授权成功，Token 已获取: {token_preview}")
                result["status"] = "no_phone"
                result["token_preview"] = token_preview
                result["access_token"] = reg_result.access_token
                result["refresh_token"] = reg_result.refresh_token
                result["workspace_id"] = reg_result.workspace_id
            elif ok:
                _log("✅ 流程完成但 Token 为空（可能是 session token 模式）")
                result["status"] = "no_phone"
            else:
                _log(f"OAuth 授权未完成: {reg_result.error_message}")
                # 如果在 _complete_post_otp_flow 内部又遇到 add_phone
                if "add-phone" in (reg_result.error_message or "").lower() or "手机" in (reg_result.error_message or ""):
                    result["status"] = "add_phone"
                else:
                    result["status"] = "no_phone_but_oauth_failed"
                    result["error"] = reg_result.error_message
        else:
            # 未知落地页，也视为可能需要人工确认
            _log(f"未知落地页面: {page_type}")
            result["status"] = "unknown"
            result["error"] = f"未知落地页: {page_type}"

    except Exception as exc:
        import traceback
        result["error"] = f"{type(exc).__name__}: {exc}"
        _log(f"测试异常: {traceback.format_exc()}")

    result["logs"] = logs[-30:]  # 只保留最后 30 条日志，节省空间
    return result


# ──────────────────────────────────────────────
# 结果统计 & 报告
# ──────────────────────────────────────────────

def _status_icon(status: str) -> str:
    return {
        "no_phone": "✅",
        "add_phone": "⚠️ ",
        "otp_needed": "📧",
        "failed": "❌",
        "no_phone_but_oauth_failed": "🔶",
        "unknown": "❓",
    }.get(status, "❓")


def print_summary(results: list):
    counts = {}
    for r in results:
        s = r.get("status", "unknown")
        counts[s] = counts.get(s, 0) + 1

    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    for status, cnt in sorted(counts.items()):
        print(f"  {_status_icon(status)} {status:<30} {cnt} 个")
    total = len(results)
    no_phone = counts.get("no_phone", 0)
    print(f"\n  总计: {total} 个账号")
    if total > 0:
        print(f"  无需手机率: {no_phone}/{total} = {no_phone/total*100:.1f}%")
    print("=" * 60)


# ──────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="对已注册账号测试 OAuth 登录是否触发 add-phone"
    )
    parser.add_argument(
        "--input", type=str,
        default=str(_ROOT / "scripts" / "pending_accounts.json"),
        help="pending_accounts.json 文件路径",
    )
    parser.add_argument(
        "--output", type=str,
        default=str(_ROOT / "scripts" / "test_results.json"),
        help="测试结果输出路径",
    )
    parser.add_argument("--proxy",  type=str, default="", help="代理地址，留空读 .env PROXY_URL")
    parser.add_argument("--limit",  type=int, default=0,  help="最多测试几个账号，0=全部")
    parser.add_argument("--delay",  type=int, default=60, help="每个账号测试间隔秒数")
    parser.add_argument(
        "--retest", action="store_true",
        help="重新测试所有账号（包括已有结果的），默认只测 pending"
    )
    args = parser.parse_args()

    proxy = args.proxy.strip() or os.getenv("PROXY_URL", "").strip() or None
    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"❌ 找不到输入文件: {input_path}")
        print("请先运行 reg_only.py 注册账号")
        sys.exit(1)

    try:
        pending_accounts = json.loads(input_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ 读取账号文件失败: {e}")
        sys.exit(1)

    # 过滤出需要测试的账号
    to_test = [
        a for a in pending_accounts
        if args.retest or a.get("status") in {"pending", "otp_needed", "unknown"}
    ]
    if args.limit > 0:
        to_test = to_test[:args.limit]

    print(f"[配置] 代理: {proxy or '无'}")
    print(f"[配置] 待测账号: {len(to_test)} 个")
    print(f"[配置] 账号间延迟: {args.delay}s")
    print()

    if not to_test:
        print("没有待测账号（所有账号已有测试结果），使用 --retest 重新测试所有账号")
        return

    all_results = []
    # 加载已有结果
    if output_path.exists():
        try:
            all_results = json.loads(output_path.read_text(encoding="utf-8"))
        except Exception:
            all_results = []

    tested_emails = {r["email"] for r in all_results}

    for idx, account in enumerate(to_test, 1):
        email = account.get("email", "")
        password = account.get("password", "")

        print(f"\n{'='*60}")
        print(f"[{idx}/{len(to_test)}] 测试账号: {email}")
        print(f"{'='*60}")

        if not email or not password:
            print(f"  ⚠️  邮箱或密码为空，跳过")
            continue

        probe_result = probe_account_phone_requirement(email, password, proxy)
        # 去掉详细 logs 节省文件大小
        probe_result_summary = {k: v for k, v in probe_result.items() if k != "logs"}

        # 更新 all_results
        all_results = [r for r in all_results if r.get("email") != email]
        all_results.append(probe_result_summary)

        # 同时更新原始 pending_accounts 里的 status
        for a in pending_accounts:
            if a.get("email") == email:
                a["status"] = probe_result["status"]
                a["last_tested_at"] = probe_result["tested_at"]
                if probe_result.get("access_token"):
                    a["access_token"] = probe_result["access_token"]
                    a["refresh_token"] = probe_result.get("refresh_token", "")
                    a["workspace_id"] = probe_result.get("workspace_id", "")

        # 保存进度
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        input_path.write_text(
            json.dumps(pending_accounts, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        icon = _status_icon(probe_result["status"])
        print(f"\n  {icon} 结果: {probe_result['status']}")
        if probe_result.get("token_preview"):
            print(f"  Token: {probe_result['token_preview']}")
        if probe_result.get("error"):
            print(f"  错误: {probe_result['error'][:120]}")

        if idx < len(to_test):
            jitter = random.uniform(-10, 10)
            wait = max(10, args.delay + int(jitter))
            print(f"\n  ⏳ 等待 {wait}s 后继续...")
            time.sleep(wait)

    print_summary(all_results)
    print(f"\n详细结果已保存到: {output_path}")
    print(f"账号状态已更新到: {input_path}")


if __name__ == "__main__":
    main()
