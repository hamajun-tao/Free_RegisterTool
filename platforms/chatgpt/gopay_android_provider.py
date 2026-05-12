"""
gopay_android_provider.py — GoPay Android 全自动支付路线

这是一条与 gopay_api（gopay_auto_register.py）**并行**的独立支付 provider。
它通过 Android 模拟器（Pixel + Google Play 镜像）运行真实 GoPay/Gojek App，
实现从设备启动到支付完成的全自动闭环——**不允许任何人工接力**。

每个阶段如果缺少必要配置，直接失败并返回明确诊断码，
不会暂停等待人工介入。

设计原则：
  - 全链路自动化，缺配置即 fail，不等人
  - 每个阶段独立可观测：安装 → 登录 → OTP → PIN → 授权 → 支付完成
  - 与 payment_auto.py 的 provider 路由集成
  - 失败时记录精确 stage + diagnostic_code

阶段定义：
  gopay_android_device_init    → 模拟器启动/设备就绪
  gopay_android_health_check   → Play Services / 网络 / App 安装
  gopay_android_app_launch     → Gojek/GoPay App 启动
  gopay_android_login          → 账号登录（输入手机号）
  gopay_android_otp_waiting    → 自动获取 OTP（SMSBower/Gojek API）
  gopay_android_otp_entered    → OTP 已输入并验证
  gopay_android_auth_ready     → GoPay 主页/授权可达
  gopay_android_pin_entry      → 自动输入 GoPay PIN
  gopay_android_payment_done   → 支付完成（状态检测）

注意：
  - 模拟器和真实 Pixel 仍有差异，部分 App 会检测 Play Integrity
  - Google Play 镜像的模拟器支持 Play Store 但不保证所有支付 SDK 信任
"""

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Optional, Callable

logger = logging.getLogger(__name__)


@dataclass
class AndroidExperimentReport:
    """GoPay Android 实验报告 — 每次运行产出一份"""
    provider: str = "gopay_android"
    device: str = ""                       # AVD 名或设备 serial
    device_image_tag: str = ""             # google_play / google_apis
    api_level: int = 0

    # 各阶段到达状态
    play_services_ok: bool = False
    play_store_ok: bool = False
    network_ok: bool = False
    app_installed: bool = False
    app_launched: bool = False
    login_reached: bool = False
    otp_stage_reached: bool = False
    otp_entered: bool = False
    auth_page_reached: bool = False
    payment_completed: bool = False

    # 诊断
    stage: str = "gopay_android_device_init"
    diagnostic_code: str = ""
    error: str = ""
    retryable: bool = True

    # 时间线
    started_at: float = 0.0
    finished_at: float = 0.0
    duration_s: float = 0.0

    # 截图路径（可选）
    screenshots: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["duration_s"] = round(self.finished_at - self.started_at, 2) if self.finished_at else 0
        return d


# ─── 阶段诊断码 ──────────────────────────────────────────────────────

_ANDROID_DIAG_CODES = {
    "no_adb": ("gopay_android_device_init", False),
    "no_avd": ("gopay_android_device_init", False),
    "emulator_boot_timeout": ("gopay_android_device_init", True),
    "play_services_missing": ("gopay_android_health_check", False),
    "network_down": ("gopay_android_health_check", True),
    "app_not_installed": ("gopay_android_health_check", True),
    "app_install_failed": ("gopay_android_health_check", False),
    "app_launch_failed": ("gopay_android_app_launch", True),
    "login_ui_not_found": ("gopay_android_login", True),
    "no_phone_number": ("gopay_android_login", False),
    "no_otp_provider": ("gopay_android_otp_waiting", False),
    "otp_input_timeout": ("gopay_android_otp_waiting", True),
    "otp_sms_read_failed": ("gopay_android_otp_waiting", True),
    "otp_verify_failed": ("gopay_android_otp_entered", True),
    "auth_page_not_reached": ("gopay_android_auth_ready", True),
    "no_gopay_pin": ("gopay_android_pin_entry", False),
    "pin_entry_failed": ("gopay_android_pin_entry", True),
    "pin_verify_failed": ("gopay_android_pin_entry", True),
    "payment_confirm_timeout": ("gopay_android_payment_done", True),
    "payment_not_confirmed": ("gopay_android_payment_done", True),
    "play_integrity_blocked": ("gopay_android_app_launch", False),
}


class GoPayAndroidProvider:
    """GoPay Android 实验 Provider

    职责：
      1. 管理 Android 设备生命周期
      2. 在设备上运行 GoPay/Gojek App 实验流程
      3. 返回结构化实验报告

    不负责：
      - Stripe checkout 创建（由 payment_auto.py 或外部调用者处理）
      - 真实扣款确认（实验阶段只做"可达性验证"）
    """

    def __init__(
        self,
        *,
        phone_number: str = "",
        gopay_pin: str = "",
        gopay_apk_path: str = "",
        gojek_apk_path: str = "",
        otp_provider: Optional[Callable[[], str]] = None,
        avd_name: str = "",
        serial: str = "",
        headless: bool = True,
        screenshot_dir: str = "",
        log_fn: Optional[Callable[[str], None]] = None,
        adb_path: str = "",
        emulator_path: str = "",
    ):
        self.phone_number = phone_number
        self.gopay_pin = gopay_pin
        self.gopay_apk_path = gopay_apk_path
        self.gojek_apk_path = gojek_apk_path
        self.otp_provider = otp_provider  # 可注入外部 OTP 来源（SMSBower 等）
        self.avd_name = avd_name
        self.serial = serial
        self.headless = headless
        self.screenshot_dir = screenshot_dir or os.path.join(
            os.path.dirname(__file__), "..", "..", "data", "android_screenshots"
        )
        self._log = log_fn or (lambda msg: logger.info("[gopay-android] %s", msg))
        self._adb_path = adb_path
        self._emulator_path = emulator_path
        self._dm = None  # lazy init

    def _device_manager(self):
        if self._dm is None:
            from platforms.chatgpt.android_device_manager import AndroidDeviceManager
            self._dm = AndroidDeviceManager(
                adb_path=self._adb_path,
                emulator_path=self._emulator_path,
                log_fn=self._log,
            )
        return self._dm

    def _take_screenshot(self, report: AndroidExperimentReport, label: str) -> str:
        """截屏并记录到报告"""
        os.makedirs(self.screenshot_dir, exist_ok=True)
        ts = int(time.time())
        filename = f"gopay_android_{label}_{ts}.png"
        local_path = os.path.join(self.screenshot_dir, filename)
        dm = self._device_manager()
        if dm.take_screenshot(self.serial, local_path):
            report.screenshots.append({"label": label, "path": local_path})
            return local_path
        return ""

    def _fail(self, report: AndroidExperimentReport, diag_code: str, error: str = "") -> AndroidExperimentReport:
        """设置失败并返回报告"""
        info = _ANDROID_DIAG_CODES.get(diag_code, (report.stage, True))
        report.stage = info[0]
        report.retryable = info[1]
        report.diagnostic_code = diag_code
        report.error = error or diag_code
        report.finished_at = time.time()
        report.duration_s = round(report.finished_at - report.started_at, 2)
        self._log(f"❌ 实验失败 stage={report.stage} diag={diag_code}: {error}")
        return report

    # ─── 主实验流程 ───────────────────────────────────────────────────

    def run_experiment(self) -> AndroidExperimentReport:
        """执行完整的 GoPay Android 实验流程

        Returns:
            AndroidExperimentReport 包含每个阶段的到达状态和诊断信息
        """
        report = AndroidExperimentReport(started_at=time.time())
        dm = self._device_manager()

        # ── Phase 1: 设备就绪 ──
        report.stage = "gopay_android_device_init"
        self._log("Phase 1: 确保 Android 设备就绪")
        try:
            self.serial, health = dm.ensure_device_ready(
                avd_name=self.avd_name,
                serial=self.serial,
                headless=self.headless,
            )
        except Exception as exc:
            error_str = str(exc)
            if "adb" in error_str.lower() or "未找到" in error_str:
                return self._fail(report, "no_adb", error_str)
            if "AVD" in error_str or "没有可用" in error_str:
                return self._fail(report, "no_avd", error_str)
            return self._fail(report, "emulator_boot_timeout", error_str)

        report.device = self.serial
        report.play_services_ok = health.play_services_ok
        report.play_store_ok = health.play_store_ok
        report.network_ok = health.network_ok

        # ── Phase 2: 健康检查 ──
        report.stage = "gopay_android_health_check"
        self._log(f"Phase 2: 设备健康检查 serial={self.serial}")
        self._log(f"  Play Services: {'✅' if health.play_services_ok else '❌'} {health.play_services_version}")
        self._log(f"  Play Store: {'✅' if health.play_store_ok else '❌'} {health.play_store_version}")
        self._log(f"  网络: {'✅' if health.network_ok else '❌'}")

        if not health.play_services_ok:
            return self._fail(report, "play_services_missing",
                              "需要 Google Play 镜像（非普通 AOSP/Google APIs）")
        if not health.network_ok:
            return self._fail(report, "network_down")

        # 检查/安装 App
        from platforms.chatgpt.android_device_manager import GOJEK_PACKAGE, GOPAY_PACKAGE
        app_pkg = GOJEK_PACKAGE  # 优先 Gojek（包含 GoPay）
        if health.gojek_installed:
            report.app_installed = True
            self._log(f"  Gojek 已安装: {health.gojek_version}")
        elif health.gopay_installed:
            app_pkg = GOPAY_PACKAGE
            report.app_installed = True
            self._log(f"  GoPay 已安装: {health.gopay_version}")
        else:
            # 尝试安装
            apk = self.gojek_apk_path or self.gopay_apk_path
            if apk and os.path.isfile(apk):
                self._log(f"  安装 APK: {apk}")
                ok = dm.install_apk(self.serial, apk)
                if not ok:
                    return self._fail(report, "app_install_failed",
                                      f"APK 安装失败: {apk}")
                report.app_installed = True
                app_pkg = GOJEK_PACKAGE if "gojek" in apk.lower() else GOPAY_PACKAGE
            else:
                return self._fail(report, "app_not_installed",
                                  "GoPay/Gojek 未安装且未提供 APK")

        self._take_screenshot(report, "health_check")

        # ── Phase 3: 启动 App ──
        report.stage = "gopay_android_app_launch"
        self._log(f"Phase 3: 启动 {app_pkg}")
        try:
            dm.launch_app(self.serial, app_pkg)
            time.sleep(5)  # 等 App 加载
            report.app_launched = True
            self._take_screenshot(report, "app_launched")
        except Exception as exc:
            return self._fail(report, "app_launch_failed", str(exc))

        # 检查是否被 Play Integrity 拦截
        ui_xml = dm.get_ui_dump(self.serial)
        if "device isn't compatible" in ui_xml.lower() or "play integrity" in ui_xml.lower():
            self._take_screenshot(report, "play_integrity_block")
            return self._fail(report, "play_integrity_blocked",
                              "App 检测到模拟器/Play Integrity 不通过")

        # ── Phase 4: 登录（必须有手机号） ──
        report.stage = "gopay_android_login"
        self._log("Phase 4: 进入登录流程")

        # ⭐ 已登录态检测：emulator 上 Gojek 已被用户手动登录时，UI 已经在首页 / 我的 / GoPay
        # 主面板等，没有手机号输入框，应跳过 Phase 4-7（注册/OTP/PIN 设置）直接进 Phase 8 等支付。
        # 已登录指示词（中/印尼/英）。注意必须从已加载完的 ui_xml 里查（Phase 3 之后已 dump）。
        signed_in_indicators = [
            "saldo", "balance", "beranda", "transfer", "topup", "top up",
            "profilku", "akun saya", "my account", "logout", "keluar",
            "kamu belum menambahkan", "gojek plus", "alamat tersimpan",
            "metode pembayaran",
        ]
        already_signed_in = any(
            ind.lower() in (ui_xml or "").lower() for ind in signed_in_indicators
        )

        if already_signed_in:
            self._log("Phase 4: 检测到 Gojek 已登录态，跳过登录/OTP/PIN，直进入支付检测")
            report.login_reached = True
            report.otp_stage_reached = True
            report.otp_entered = True
            report.auth_page_reached = True
            self._take_screenshot(report, "already_signed_in")
            # 跳到 Phase 8（让外层 ChatGPT checkout 触发支付，emulator 这边等支付确认）
            payment_done = self._wait_payment_done(dm, report)
            return self._finalize_payment_phase(report, payment_done)

        if not self.phone_number:
            return self._fail(report, "no_phone_number",
                              "未配置 payment_gopay_phone，无法自动登录")

        # 尝试在 UI 中找到手机号输入框
        login_found = False
        _LOGIN_KEYWORDS = ["phone", "nomor", "number", "手机", "telepon", "masuk", "login"]
        for keyword in _LOGIN_KEYWORDS:
            if keyword.lower() in ui_xml.lower():
                login_found = True
                break
        if not login_found:
            # 可能在 splash/onboarding，尝试自动跳过
            for _ in range(5):
                dm.tap(self.serial, 540, 1800)  # 底部可能有 "Login" / "下一步"
                time.sleep(3)
                ui_xml = dm.get_ui_dump(self.serial)
                for keyword in _LOGIN_KEYWORDS:
                    if keyword.lower() in ui_xml.lower():
                        login_found = True
                        break
                if login_found:
                    break
                # 也试屏幕中部
                dm.tap(self.serial, 540, 960)
                time.sleep(2)

        if not login_found:
            self._take_screenshot(report, "login_not_found")
            return self._fail(report, "login_ui_not_found",
                              "未找到登录/手机号输入界面")

        report.login_reached = True
        self._take_screenshot(report, "login_page")

        # 输入手机号
        self._log(f"  输入手机号: {self.phone_number[:4]}****")
        dm.tap(self.serial, 540, 900)
        time.sleep(1)
        dm.input_text(self.serial, self.phone_number)
        time.sleep(1)
        dm.tap(self.serial, 540, 1200)  # 点击继续/发送 OTP
        time.sleep(3)
        self._take_screenshot(report, "after_phone_input")

        # ── Phase 5: OTP（必须有 otp_provider，不等人） ──
        report.stage = "gopay_android_otp_waiting"
        report.otp_stage_reached = True
        self._log("Phase 5: 自动获取 OTP")

        if not self.otp_provider:
            self._take_screenshot(report, "no_otp_provider")
            return self._fail(report, "no_otp_provider",
                              "未配置 OTP 自动获取（需 smsbower_api_key），无法继续")

        # OTP 重试（最多 3 次）
        otp = ""
        otp_max_retries = 3
        for otp_attempt in range(1, otp_max_retries + 1):
            try:
                otp = self.otp_provider()
                if otp:
                    self._log(f"  OTP 收到 (attempt {otp_attempt}): {otp[:2]}****")
                    break
            except Exception as otp_exc:
                self._log(f"  OTP 获取失败 (attempt {otp_attempt}/{otp_max_retries}): {otp_exc}")
                if otp_attempt < otp_max_retries:
                    # 尝试重新触发 OTP：重新点击发送按钮
                    dm.press_key(self.serial, "KEYCODE_BACK")
                    time.sleep(2)
                    dm.tap(self.serial, 540, 1200)
                    time.sleep(3)
                    continue
                self._take_screenshot(report, "otp_timeout")
                return self._fail(report, "otp_input_timeout",
                                  f"OTP 获取失败（已重试 {otp_max_retries} 次）: {otp_exc}")

        # 也尝试从设备通知栏读取 OTP（SMS auto-read fallback）
        if not otp:
            self._log("  尝试从设备 SMS 中读取 OTP...")
            try:
                sms_output = dm._run_shell(
                    self.serial,
                    "content", "query",
                    "--uri", "content://sms/inbox",
                    "--projection", "body",
                    "--where", "\"date > " + str(int((time.time() - 120) * 1000)) + "\"",
                    "--sort", "date DESC",
                    timeout=10,
                )
                # 从最近 SMS 中提取 4-6 位数字验证码
                otp_match = re.search(r'\b(\d{4,6})\b', sms_output or "")
                if otp_match:
                    otp = otp_match.group(1)
                    self._log(f"  从 SMS 读取 OTP: {otp[:2]}****")
            except Exception as sms_exc:
                self._log(f"  SMS 读取失败: {sms_exc}")

        if not otp:
            self._take_screenshot(report, "otp_all_failed")
            return self._fail(report, "otp_sms_read_failed",
                              "SMSBower + 设备 SMS 均未获取到 OTP")

        # 输入 OTP
        report.stage = "gopay_android_otp_entered"
        dm.input_text(self.serial, otp)
        time.sleep(2)
        dm.tap(self.serial, 540, 1200)  # 确认
        time.sleep(5)
        report.otp_entered = True
        self._take_screenshot(report, "otp_entered")

        # 检查 OTP 验证是否成功（不是停在错误页）
        ui_xml = dm.get_ui_dump(self.serial)
        otp_error_indicators = ["salah", "wrong", "invalid", "error", "gagal", "failed"]
        if any(ind.lower() in ui_xml.lower() for ind in otp_error_indicators):
            self._take_screenshot(report, "otp_verify_failed")
            return self._fail(report, "otp_verify_failed",
                              "OTP 验证失败（App 显示错误信息）")

        # ── Phase 6: 授权页/GoPay 主页 ──
        report.stage = "gopay_android_auth_ready"
        ui_xml = dm.get_ui_dump(self.serial)
        auth_indicators = ["gopay", "saldo", "balance", "pay", "bayar", "PIN", "beranda", "home"]
        auth_reached = any(ind.lower() in ui_xml.lower() for ind in auth_indicators)

        if not auth_reached:
            # 等几秒重试（App 可能在加载）
            for _ in range(3):
                time.sleep(3)
                ui_xml = dm.get_ui_dump(self.serial)
                auth_reached = any(ind.lower() in ui_xml.lower() for ind in auth_indicators)
                if auth_reached:
                    break

        report.auth_page_reached = auth_reached
        self._take_screenshot(report, "auth_check")

        if not auth_reached:
            return self._fail(report, "auth_page_not_reached",
                              "OTP 后未到达 GoPay 主页/授权页")

        self._log("✅ GoPay 授权页/主页可达")

        # ── Phase 7: PIN 输入（必须有 gopay_pin） ──
        report.stage = "gopay_android_pin_entry"
        self._log("Phase 7: 输入 GoPay PIN")

        if not self.gopay_pin:
            return self._fail(report, "no_gopay_pin",
                              "未配置 payment_gopay_pin，无法自动完成 PIN 验证")

        # 检查是否有 PIN 输入界面（可能需要先触发支付动作）
        pin_indicators = ["pin", "masukkan pin", "enter pin", "verifikasi"]
        pin_ui_found = any(ind.lower() in ui_xml.lower() for ind in pin_indicators)

        if not pin_ui_found:
            # 尝试触发需要 PIN 的操作（如点击支付/transfer 按钮）
            self._log("  尝试触发 PIN 输入界面...")
            pay_indicators = ["pay", "bayar", "transfer", "kirim"]
            for ind in pay_indicators:
                if ind.lower() in ui_xml.lower():
                    # 找到支付相关按钮区域，尝试点击
                    dm.tap(self.serial, 540, 1400)
                    time.sleep(3)
                    break
            ui_xml = dm.get_ui_dump(self.serial)
            pin_ui_found = any(ind.lower() in ui_xml.lower() for ind in pin_indicators)

        if pin_ui_found:
            # 逐位输入 PIN（GoPay 通常是 6 位数字键盘）
            self._log(f"  输入 PIN: ******")
            for digit in self.gopay_pin:
                dm.input_text(self.serial, digit)
                time.sleep(0.3)
            time.sleep(3)
            self._take_screenshot(report, "pin_entered")

            # 检查 PIN 验证结果
            ui_xml = dm.get_ui_dump(self.serial)
            pin_error_indicators = ["salah", "wrong", "invalid", "gagal", "blocked", "error"]
            if any(ind.lower() in ui_xml.lower() for ind in pin_error_indicators):
                self._take_screenshot(report, "pin_verify_failed")
                return self._fail(report, "pin_verify_failed",
                                  "GoPay PIN 验证失败")
            self._log("✅ PIN 输入完成")
        else:
            self._log("  当前页面无 PIN 输入界面（可能不需要 PIN 或需其他触发）")

        # ── Phase 8: 支付完成检测 ──
        report.stage = "gopay_android_payment_done"
        self._log("Phase 8: 检测支付完成状态")

        # 等待支付处理（最长 30 秒轮询）
        payment_done = False
        success_indicators = [
            "berhasil", "success", "sukses", "completed", "selesai",
            "pembayaran berhasil", "payment successful", "terima kasih",
            "thank you", "receipt",
        ]
        for poll in range(10):
            time.sleep(3)
            ui_xml = dm.get_ui_dump(self.serial)
            if any(ind.lower() in ui_xml.lower() for ind in success_indicators):
                payment_done = True
                break
            # 检查是否回到了外部浏览器/Stripe 回跳
            if "stripe" in ui_xml.lower() or "openai" in ui_xml.lower():
                payment_done = True
                break

        report.payment_completed = payment_done
        self._take_screenshot(report, "payment_result")

        if payment_done:
            report.diagnostic_code = ""
            self._log("✅ 支付完成")
        else:
            # 不是 hard fail，标记为 soft 超时：流程走完但未检测到成功确认
            report.diagnostic_code = "payment_confirm_timeout"
            report.error = "支付流程执行完毕但未检测到成功确认页面"
            self._log("⚠️ 未检测到支付成功确认（可能成功但 UI 未匹配）")

        # ── 完成 ──
        report.finished_at = time.time()
        report.duration_s = round(report.finished_at - report.started_at, 2)
        self._log(f"流程完成: stage={report.stage} payment_done={payment_done} duration={report.duration_s}s")
        self._log(f"  报告: {json.dumps(report.to_dict(), ensure_ascii=False, indent=2)[:500]}")
        return report


def run_gopay_android_experiment(
    cfg: dict,
    *,
    log_fn: Optional[Callable[[str], None]] = None,
) -> AndroidExperimentReport:
    """供 payment_auto.py 调用的全自动入口

    必须配置：
      - payment_gopay_phone       （手机号）
      - payment_gopay_pin         （GoPay PIN）
      - smsbower_api_key          （OTP 自动接收）

    可选配置：
      - payment_android_avd_name       AVD 名称
      - payment_android_serial         设备 serial
      - payment_android_headless       无头模式 (default 1)
      - payment_android_gojek_apk      Gojek APK 路径
      - payment_android_gopay_apk      GoPay APK 路径
      - payment_android_adb_path       ADB 路径覆盖
      - payment_android_emulator_path  emulator 路径覆盖
      - payment_gopay_sms_country      SMS 国家码 (default 6=印尼)
      - payment_android_otp_retries    OTP 重试次数 (default 3)

    全自动设计：缺少任何必要配置直接返回失败报告，不会暂停等人。
    """
    _log = log_fn or (lambda msg: logger.info("[gopay-android] %s", msg))

    phone = str(cfg.get("payment_gopay_phone") or "").strip()
    pin = str(cfg.get("payment_gopay_pin") or "").strip()
    avd = str(cfg.get("payment_android_avd_name") or "").strip()
    serial = str(cfg.get("payment_android_serial") or "").strip()
    headless = str(cfg.get("payment_android_headless") or "1").strip().lower() not in ("0", "false", "no")
    gojek_apk = str(cfg.get("payment_android_gojek_apk") or "").strip()
    gopay_apk = str(cfg.get("payment_android_gopay_apk") or "").strip()
    adb_path = str(cfg.get("payment_android_adb_path") or "").strip()
    emu_path = str(cfg.get("payment_android_emulator_path") or "").strip()
    smsbower_key = str(cfg.get("smsbower_api_key") or "").strip()
    sms_country = str(cfg.get("payment_gopay_sms_country") or "6").strip()
    proxy_url = str(cfg.get("proxy_url") or "").strip()
    # ⭐ 同一个号的收码依据：API 阶段注册好后保留下来的 SMSBower activation_id
    saved_activation_id = str(cfg.get("payment_gopay_sms_activation_id") or "").strip()
    # OTP 兜底文件 / URL（适用于手动配号码、WhatsApp relay 等非 SMSBower 收码场景）
    otp_file = str(cfg.get("payment_gopay_otp_file") or "").strip()
    otp_url = str(cfg.get("payment_gopay_otp_url") or "").strip()
    try:
        otp_timeout_s = int(cfg.get("payment_gopay_otp_timeout") or cfg.get("smsbower_otp_timeout_seconds") or 240)
    except Exception:
        otp_timeout_s = 240

    # ── 前置校验：缺必要配置直接报错 ──
    if not phone:
        report = AndroidExperimentReport(started_at=time.time())
        report.finished_at = time.time()
        report.stage = "gopay_android_login"
        report.diagnostic_code = "no_phone_number"
        report.retryable = False
        report.error = (
            "缺少 payment_gopay_phone 配置；"
            "请预先配置号码，或开启 payment_gopay_auto_register=1 让上游自动注册一个 GoPay 账号并带出 activation_id。"
        )
        return report

    if not pin:
        report = AndroidExperimentReport(started_at=time.time())
        report.finished_at = time.time()
        report.stage = "gopay_android_pin_entry"
        report.diagnostic_code = "no_gopay_pin"
        report.retryable = False
        report.error = "缺少 payment_gopay_pin 配置"
        return report

    # 至少要有一种 OTP 来源：SMSBower activation / OTP 文件 / OTP URL
    if not (saved_activation_id or otp_file or otp_url):
        report = AndroidExperimentReport(started_at=time.time())
        report.finished_at = time.time()
        report.stage = "gopay_android_otp_waiting"
        report.diagnostic_code = "no_otp_provider"
        report.retryable = False
        report.error = (
            "找不到任何 OTP 来源：payment_gopay_sms_activation_id / payment_gopay_otp_file / "
            "payment_gopay_otp_url 三者至少需要配置一个。"
            "推荐做法：开启 payment_gopay_auto_register=1，由上游注册 GoPay 账号并自动带出 activation_id。"
        )
        return report

    # ── OTP provider：优先复用同一个号的 activation，否则走 OTP 文件/URL 兜底 ──
    def _read_otp_from_file(path: str, timeout: int) -> str:
        """WhatsApp relay 等场景：轮询读文件，拿到 4~8 位数字串作为 OTP"""
        import os as _os
        import re as _re
        if not path:
            return ""
        deadline = time.time() + timeout
        last_mtime = 0.0
        while time.time() < deadline:
            try:
                if _os.path.isfile(path):
                    mt = _os.path.getmtime(path)
                    if mt > last_mtime:
                        last_mtime = mt
                        with open(path, "r", encoding="utf-8", errors="ignore") as f:
                            text = f.read()
                        m = _re.search(r"\b(\d{4,8})\b", text)
                        if m:
                            return m.group(1)
            except Exception:
                pass
            time.sleep(3)
        return ""

    def _read_otp_from_url(url: str, timeout: int) -> str:
        import re as _re
        try:
            import requests as _rq
        except ImportError:
            return ""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                r = _rq.get(url, timeout=10)
                if r.status_code == 200:
                    m = _re.search(r"\b(\d{4,8})\b", r.text or "")
                    if m:
                        return m.group(1)
            except Exception:
                pass
            time.sleep(3)
        return ""

    def _auto_otp_provider() -> str:
        """OTP 获取策略（严格遵守"登录号 = 收码号"原则）：

        优先级：
        1. saved_activation_id + SMSBower API key → 复用上游注册产生的 activation，同一个号收下一条 OTP
        2. otp_file → 轮询文件拿 OTP（如 WhatsApp relay 写入的 runtime/wa_relay/wa-otp.txt）
        3. otp_url → 轮询 URL 拿 OTP

        绝对不能：新买一个号来收 OTP —— 那样 App 内登录号和收码号不一致，永远拿不到正确 OTP。
        """
        if saved_activation_id and smsbower_key:
            try:
                from platforms.chatgpt.gopay_auto_register import GoPayRegistrar
            except ImportError as e:
                _log(f"  [OTP] GoPayRegistrar 导入失败: {e}，fallback 到文件/URL")
            else:
                reg = GoPayRegistrar(
                    smsbower_api_key=smsbower_key,
                    proxy_url=proxy_url,
                    log_fn=_log,
                    sms_country=sms_country,
                    otp_timeout_seconds=otp_timeout_s,
                )
                # 复用已有 activation_id（⭐ 不再 acquire_phone，避免买新号）
                reg._activation_id = saved_activation_id
                _log(f"  [OTP] 复用已有 SMSBower activation_id={saved_activation_id}（同一个号收码）")
                try:
                    code = reg.receive_otp()
                    if code:
                        return code
                except Exception as exc:
                    _log(f"  [OTP] SMSBower receive_otp 失败: {exc}")

        if otp_file:
            _log(f"  [OTP] fallback 读 OTP 文件: {otp_file} (timeout={otp_timeout_s}s)")
            code = _read_otp_from_file(otp_file, otp_timeout_s)
            if code:
                return code

        if otp_url:
            _log(f"  [OTP] fallback 读 OTP URL: {otp_url} (timeout={otp_timeout_s}s)")
            code = _read_otp_from_url(otp_url, otp_timeout_s)
            if code:
                return code

        raise RuntimeError(
            "OTP 获取失败：activation/文件/URL 全部未产出 OTP。"
            "若登录号不是 SMSBower 号，请确保 payment_gopay_otp_file/url 能产出对应 OTP。"
        )

    provider = GoPayAndroidProvider(
        phone_number=phone,
        gopay_pin=pin,
        gopay_apk_path=gopay_apk,
        gojek_apk_path=gojek_apk,
        otp_provider=_auto_otp_provider,
        avd_name=avd,
        serial=serial,
        headless=headless,
        log_fn=_log,
        adb_path=adb_path,
        emulator_path=emu_path,
    )

    return provider.run_experiment()
