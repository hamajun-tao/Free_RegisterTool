"""核对 GoPay+LuckMail+日本免费+WhatsApp 路径必需的所有配置项"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.config_store import config_store as c  # noqa: E402

REQUIRED = [
    # ----- 邮箱 -----
    ("mail_provider", "luckmail", "邮箱服务商"),
    ("luckmail_api_key", None, "LuckMail API Key"),
    # ----- 注册/OAuth 路径 -----
    ("payment_method", "gopay", "支付方式"),
    ("payment_provider", "gopay_android", "支付执行器"),
    ("payment_auto_plan", "plus", "目标套餐"),
    # ----- 日本免费 promo -----
    ("payment_promo_proxy_url", None, "日本 promo 代理"),
    ("payment_promo_proxy_geo", "JP", "promo 代理地区"),
    # ----- 印尼账单 -----
    ("payment_billing_country", "ID", "账单国家"),
    ("payment_billing_currency", "IDR", "账单货币"),
    ("payment_billing_name", None, "账单姓名"),
    ("payment_billing_address", None, "账单地址"),
    ("payment_billing_city", None, "账单城市"),
    ("payment_billing_state", None, "账单省"),
    ("payment_billing_zip", None, "账单邮编"),
    # ----- GoPay -----
    ("payment_gopay_phone", None, "GoPay 手机号 (WhatsApp 可收)"),
    ("payment_gopay_pin", None, "GoPay PIN"),
    # ----- 验证码 -----
    ("payment_captcha_key", None, "Captcha API Key"),
    # ----- WhatsApp OTP relay -----
    ("payment_gopay_otp_file", "runtime/wa_relay/wa-otp.txt", "WhatsApp OTP 文件"),
    # ----- Android -----
    ("payment_android_avd_name", None, "Android AVD 名称(可空,自动检测 serial)"),
    ("payment_android_serial", None, "Android 设备 serial(二选一)"),
    ("payment_android_headless", None, "Android 无头模式(可空 默认1)"),
    # ----- sub2api -----
    ("sub2api_api_url", None, "sub2api 接口"),
    ("sub2api_api_key", None, "sub2api Key"),
]

print(f"{'KEY':<35} {'CURRENT':<55} {'STATUS'}")
print("-" * 110)
missing = []
mismatched = []
for key, expect, desc in REQUIRED:
    v = c.get(key)
    sv = str(v) if v is not None else ""
    short = sv if len(sv) <= 55 else sv[:52] + "..."
    if not sv.strip():
        # 允许 android 二选一 / headless / avd
        if key in ("payment_android_avd_name", "payment_android_serial", "payment_android_headless"):
            status = "  (空, 可选)"
        else:
            status = "[MISSING]"
            missing.append((key, desc))
    elif expect is not None and sv.strip().lower() != str(expect).lower():
        status = f"[MISMATCH expected={expect}]"
        mismatched.append((key, sv, expect))
    else:
        status = "OK"
    print(f"{key:<35} {short:<55} {status}")

# Android serial / avd 至少一个
serial = (c.get("payment_android_serial") or "").strip()
avd = (c.get("payment_android_avd_name") or "").strip()
if not serial and not avd:
    print("\n[WARN] payment_android_serial 与 payment_android_avd_name 都为空 → 需要至少一个")
    missing.append(("payment_android_serial|avd_name", "Android 设备 serial 或 AVD"))

if missing:
    print(f"\n=== 缺失 {len(missing)} 项必填 ===")
    for k, d in missing:
        print(f"  - {k}  ({d})")
if mismatched:
    print(f"\n=== {len(mismatched)} 项值不匹配，需修正 ===")
    for k, v, e in mismatched:
        print(f"  - {k}: 当前={v!r}  期望={e!r}")

if not missing and not mismatched:
    print("\n[ALL OK] 配置完整，可直接跑 GoPay 流程")
