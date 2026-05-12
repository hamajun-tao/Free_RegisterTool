"""
android_device_manager.py — Android 模拟器设备管理

通过 ADB 管理 Pixel/Google Play 镜像的模拟器实例：
  - 枚举可用 AVD / 已连接设备
  - 启动/关闭模拟器
  - 设备健康检查（Play Services / Play Store / 网络 / 屏幕状态）
  - App 安装状态检测（GoPay / Gojek）
  - ADB shell 命令执行
  - 屏幕截图 / UI dump

设计原则：
  - 不侵入现有支付流程，作为独立设备层
  - 所有操作返回结构化 dict，便于上层 provider 消费
  - 对 ADB 的依赖通过 PATH 发现，不硬编码路径

镜像选择指南（来自 Android 官方 AVD 文档）：
  - Google APIs 镜像：包含 Play Services，不含 Play Store，可 root
  - Google Play 镜像：包含 Play Store + Play Services，release key 签名，不能 root
  - 支付 App 测试应优先用 Google Play 镜像（完整 Play 生态）
"""

import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field, asdict
from typing import Optional, Callable

logger = logging.getLogger(__name__)

# GoPay / Gojek 包名
GOPAY_PACKAGE = "com.gojek.gopay"
GOJEK_PACKAGE = "com.gojek.app"
PLAY_STORE_PACKAGE = "com.android.vending"
PLAY_SERVICES_PACKAGE = "com.google.android.gms"

# 推荐的 AVD 镜像名前缀
RECOMMENDED_IMAGE_TAGS = ("google_play", "google_apis_playstore")


@dataclass
class DeviceInfo:
    """设备/模拟器基本信息"""
    serial: str                     # adb serial（如 emulator-5554）
    model: str = ""                 # 设备型号
    android_version: str = ""       # Android 版本号
    api_level: int = 0              # API level
    is_emulator: bool = True
    state: str = "unknown"          # device / offline / unauthorized


@dataclass
class DeviceHealthReport:
    """设备健康检查报告"""
    serial: str
    device_online: bool = False
    play_services_ok: bool = False
    play_services_version: str = ""
    play_store_ok: bool = False
    play_store_version: str = ""
    network_ok: bool = False
    screen_on: bool = False
    gopay_installed: bool = False
    gopay_version: str = ""
    gojek_installed: bool = False
    gojek_version: str = ""
    errors: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AVDInfo:
    """本地 AVD 配置信息"""
    name: str
    target: str = ""       # 如 "google_apis_playstore"
    api_level: int = 0
    device: str = ""       # 如 "pixel_8"
    tag: str = ""          # 如 "google_play"


class ADBError(Exception):
    """ADB 命令执行失败"""


class AndroidDeviceManager:
    """Android 设备/模拟器管理器"""

    def __init__(
        self,
        *,
        adb_path: str = "",
        emulator_path: str = "",
        avdmanager_path: str = "",
        log_fn: Optional[Callable[[str], None]] = None,
        command_timeout: int = 30,
    ):
        self.adb = adb_path or shutil.which("adb") or "adb"
        self.emulator = emulator_path or shutil.which("emulator") or "emulator"
        self.avdmanager = avdmanager_path or shutil.which("avdmanager") or "avdmanager"
        self._log = log_fn or (lambda msg: logger.info("[android-dm] %s", msg))
        self.timeout = command_timeout

    # ─── ADB 底层 ─────────────────────────────────────────────────────

    def _run_adb(self, *args: str, serial: str = "", timeout: int = 0) -> str:
        """执行 adb 命令，返回 stdout"""
        cmd = [self.adb]
        if serial:
            cmd.extend(["-s", serial])
        cmd.extend(args)
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
                encoding="utf-8",
                errors="replace",
            )
            if proc.returncode != 0:
                stderr = (proc.stderr or "").strip()
                if stderr:
                    self._log(f"adb stderr: {stderr[:200]}")
            return proc.stdout.strip()
        except subprocess.TimeoutExpired:
            raise ADBError(f"adb 超时: {' '.join(cmd)}")
        except FileNotFoundError:
            raise ADBError(f"adb 未找到: {self.adb}")

    def _run_shell(self, serial: str, *args: str, timeout: int = 0) -> str:
        """执行 adb shell 命令"""
        return self._run_adb("shell", *args, serial=serial, timeout=timeout)

    # ─── 设备枚举 ─────────────────────────────────────────────────────

    def list_devices(self) -> list[DeviceInfo]:
        """列出所有已连接的 adb 设备"""
        output = self._run_adb("devices", "-l")
        devices = []
        for line in output.splitlines()[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            serial = parts[0]
            state = parts[1]
            model = ""
            for p in parts[2:]:
                if p.startswith("model:"):
                    model = p.split(":", 1)[1]
            is_emu = serial.startswith("emulator-") or "emulator" in line.lower()
            dev = DeviceInfo(serial=serial, model=model, state=state, is_emulator=is_emu)
            if state == "device":
                try:
                    dev.android_version = self._run_shell(serial, "getprop", "ro.build.version.release")
                    api_str = self._run_shell(serial, "getprop", "ro.build.version.sdk")
                    dev.api_level = int(api_str) if api_str.isdigit() else 0
                except Exception:
                    pass
            devices.append(dev)
        return devices

    def list_avds(self) -> list[AVDInfo]:
        """列出本地已创建的 AVD"""
        try:
            proc = subprocess.run(
                [self.emulator, "-list-avds"],
                capture_output=True, text=True, timeout=15,
                encoding="utf-8", errors="replace",
            )
            names = [n.strip() for n in proc.stdout.strip().splitlines() if n.strip()]
        except Exception as exc:
            self._log(f"枚举 AVD 失败: {exc}")
            return []

        avds = []
        for name in names:
            avd = AVDInfo(name=name)
            # 尝试解析 config.ini
            avd_home = os.environ.get("ANDROID_AVD_HOME") or os.path.expanduser("~/.android/avd")
            ini_path = os.path.join(avd_home, f"{name}.avd", "config.ini")
            if os.path.isfile(ini_path):
                try:
                    with open(ini_path, "r", encoding="utf-8") as f:
                        for line in f:
                            k, _, v = line.partition("=")
                            k, v = k.strip(), v.strip()
                            if k == "tag.id":
                                avd.tag = v
                            elif k == "hw.device.name":
                                avd.device = v
                            elif k == "image.sysdir.1":
                                m = re.search(r"android-(\d+)", v)
                                if m:
                                    avd.api_level = int(m.group(1))
                                if "google_play" in v:
                                    avd.tag = avd.tag or "google_play"
                except Exception:
                    pass
            avds.append(avd)
        return avds

    def find_best_avd(self) -> Optional[str]:
        """找到最适合支付测试的 AVD（Google Play 镜像优先）"""
        avds = self.list_avds()
        # 优先级：google_play tag > google_apis > 其他；API 越高越好
        scored = []
        for avd in avds:
            score = avd.api_level
            if avd.tag in ("google_play", "google_apis_playstore"):
                score += 1000  # Google Play 镜像大优先
            elif "google" in avd.tag.lower():
                score += 500   # Google APIs
            scored.append((score, avd.name))
        scored.sort(reverse=True)
        return scored[0][1] if scored else None

    # ─── 模拟器启动/关闭 ─────────────────────────────────────────────

    def start_emulator(
        self,
        avd_name: str,
        *,
        port: int = 5554,
        headless: bool = False,
        extra_args: list[str] | None = None,
        wait_boot_timeout: int = 120,
    ) -> str:
        """启动模拟器，返回 serial（如 emulator-5554）"""
        serial = f"emulator-{port}"
        # 检查是否已在运行
        for dev in self.list_devices():
            if dev.serial == serial and dev.state == "device":
                self._log(f"模拟器 {serial} 已在运行")
                return serial

        cmd = [
            self.emulator, "-avd", avd_name,
            "-port", str(port),
            "-no-snapshot-save",
            "-no-audio",
        ]
        if headless:
            cmd.append("-no-window")
        if extra_args:
            cmd.extend(extra_args)

        self._log(f"启动模拟器: {' '.join(cmd)}")
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )

        # 等待 boot 完成
        self._log(f"等待模拟器 {serial} 启动 (timeout={wait_boot_timeout}s)...")
        deadline = time.time() + wait_boot_timeout
        while time.time() < deadline:
            try:
                boot = self._run_shell(serial, "getprop", "sys.boot_completed", timeout=5)
                if boot.strip() == "1":
                    self._log(f"模拟器 {serial} 启动完成")
                    return serial
            except Exception:
                pass
            time.sleep(3)
        raise ADBError(f"模拟器 {serial} 启动超时 ({wait_boot_timeout}s)")

    def stop_emulator(self, serial: str):
        """关闭模拟器"""
        try:
            self._run_adb("emu", "kill", serial=serial, timeout=10)
            self._log(f"模拟器 {serial} 已关闭")
        except Exception as exc:
            self._log(f"关闭模拟器 {serial} 失败: {exc}")

    # ─── 设备健康检查 ─────────────────────────────────────────────────

    def _get_package_version(self, serial: str, package: str) -> str:
        """获取已安装包的版本"""
        try:
            output = self._run_shell(serial, "dumpsys", "package", package)
            for line in output.splitlines():
                line = line.strip()
                if line.startswith("versionName="):
                    return line.split("=", 1)[1].strip()
            return ""
        except Exception:
            return ""

    def _is_package_installed(self, serial: str, package: str) -> bool:
        """检查包是否已安装"""
        try:
            output = self._run_shell(serial, "pm", "list", "packages", package)
            return f"package:{package}" in output
        except Exception:
            return False

    def check_device_health(self, serial: str) -> DeviceHealthReport:
        """对指定设备执行完整健康检查"""
        report = DeviceHealthReport(serial=serial)

        # 1. 设备在线？
        try:
            state = self._run_adb("get-state", serial=serial, timeout=5)
            report.device_online = state.strip() == "device"
        except Exception as exc:
            report.errors.append(f"device_state: {exc}")
            return report

        if not report.device_online:
            report.errors.append("device not online")
            return report

        # 2. Google Play Services
        report.play_services_ok = self._is_package_installed(serial, PLAY_SERVICES_PACKAGE)
        if report.play_services_ok:
            report.play_services_version = self._get_package_version(serial, PLAY_SERVICES_PACKAGE)

        # 3. Google Play Store
        report.play_store_ok = self._is_package_installed(serial, PLAY_STORE_PACKAGE)
        if report.play_store_ok:
            report.play_store_version = self._get_package_version(serial, PLAY_STORE_PACKAGE)

        # 4. 网络
        # 注意：宿主机若启用 Tailscale / Clash TUN 模式，emulator 的 ICMP 往外网会被吞掉，
        # 即使 -http-proxy 已正常工作 ping 也会失败，导致误判 network_down。
        # 改用 Android ConnectivityManager 报告的 active network validated 状态判断。
        report.network_ok = False
        try:
            conn_out = self._run_shell(serial, "dumpsys", "connectivity", timeout=10)
            # 优先看 NetworkAgentInfo 里的 validated=true（Android 验证过有真实出口）
            if "validated=true" in conn_out.lower():
                report.network_ok = True
            else:
                # 退一步：是否存在 Validated（V）或 CONNECTED 的 active network
                for line in conn_out.splitlines():
                    s = line.strip()
                    if "Active default network" in s and "null" not in s.lower():
                        report.network_ok = True
                        break
                    if s.startswith("NetworkAgentInfo") and "CONNECTED/CONNECTED" in s:
                        report.network_ok = True
                        break
        except Exception:
            report.network_ok = False
        # 仍未通过时尝试 ICMP 兜底（部分环境无 TUN 拦截）
        if not report.network_ok:
            try:
                ping_out = self._run_shell(serial, "ping", "-c", "1", "-W", "3", "8.8.8.8", timeout=10)
                if "1 received" in ping_out or "1 packets received" in ping_out:
                    report.network_ok = True
            except Exception:
                pass

        # 5. 屏幕
        try:
            display = self._run_shell(serial, "dumpsys", "display")
            report.screen_on = "mScreenState=ON" in display or "state=ON" in display
        except Exception:
            pass

        # 6. GoPay
        report.gopay_installed = self._is_package_installed(serial, GOPAY_PACKAGE)
        if report.gopay_installed:
            report.gopay_version = self._get_package_version(serial, GOPAY_PACKAGE)

        # 7. Gojek
        report.gojek_installed = self._is_package_installed(serial, GOJEK_PACKAGE)
        if report.gojek_installed:
            report.gojek_version = self._get_package_version(serial, GOJEK_PACKAGE)

        if not report.play_services_ok:
            report.errors.append("Play Services 未安装（需要 Google Play 镜像）")
        if not report.network_ok:
            report.errors.append("网络不通")

        return report

    # ─── App 操作 ─────────────────────────────────────────────────────

    def install_apk(self, serial: str, apk_path: str) -> bool:
        """安装 APK"""
        if not os.path.isfile(apk_path):
            raise ADBError(f"APK 不存在: {apk_path}")
        try:
            output = self._run_adb("install", "-r", apk_path, serial=serial, timeout=120)
            ok = "Success" in output
            self._log(f"安装 {'成功' if ok else '失败'}: {apk_path}")
            return ok
        except Exception as exc:
            self._log(f"安装失败: {exc}")
            return False

    def launch_app(self, serial: str, package: str, activity: str = "") -> bool:
        """启动 App"""
        if activity:
            component = f"{package}/{activity}"
            self._run_shell(serial, "am", "start", "-n", component)
        else:
            self._run_shell(
                serial, "monkey", "-p", package,
                "-c", "android.intent.category.LAUNCHER", "1",
            )
        self._log(f"已启动: {package}")
        return True

    def force_stop_app(self, serial: str, package: str):
        """强制停止 App"""
        self._run_shell(serial, "am", "force-stop", package)

    def clear_app_data(self, serial: str, package: str):
        """清除 App 数据"""
        self._run_shell(serial, "pm", "clear", package)
        self._log(f"已清除 {package} 数据")

    def take_screenshot(self, serial: str, local_path: str) -> bool:
        """截屏并拉取到本地"""
        remote = "/sdcard/screenshot_tmp.png"
        try:
            self._run_shell(serial, "screencap", "-p", remote)
            self._run_adb("pull", remote, local_path, serial=serial, timeout=15)
            self._run_shell(serial, "rm", remote)
            return os.path.isfile(local_path)
        except Exception as exc:
            self._log(f"截屏失败: {exc}")
            return False

    def get_ui_dump(self, serial: str) -> str:
        """获取当前界面 UI 层级 XML"""
        remote = "/sdcard/ui_dump.xml"
        try:
            self._run_shell(serial, "uiautomator", "dump", remote, timeout=15)
            output = self._run_shell(serial, "cat", remote)
            self._run_shell(serial, "rm", remote)
            return output
        except Exception as exc:
            self._log(f"UI dump 失败: {exc}")
            return ""

    def tap(self, serial: str, x: int, y: int):
        """模拟触摸点击"""
        self._run_shell(serial, "input", "tap", str(x), str(y))

    def input_text(self, serial: str, text: str):
        """输入文本"""
        escaped = text.replace(" ", "%s").replace("&", "\\&")
        self._run_shell(serial, "input", "text", escaped)

    def press_key(self, serial: str, keycode: str):
        """按键事件（如 KEYCODE_BACK, KEYCODE_HOME）"""
        self._run_shell(serial, "input", "keyevent", keycode)

    # ─── 便捷方法 ─────────────────────────────────────────────────────

    def ensure_device_ready(
        self,
        avd_name: str = "",
        serial: str = "",
        headless: bool = True,
    ) -> tuple[str, DeviceHealthReport]:
        """确保有可用设备，返回 (serial, health_report)

        优先使用已连接的设备；没有则尝试启动指定/最佳 AVD。
        """
        # 1. 检查已连接设备
        if serial:
            report = self.check_device_health(serial)
            if report.device_online:
                return serial, report

        devices = self.list_devices()
        online = [d for d in devices if d.state == "device"]
        if online:
            best = online[0]
            report = self.check_device_health(best.serial)
            return best.serial, report

        # 2. 启动模拟器
        if not avd_name:
            avd_name = self.find_best_avd() or ""
        if not avd_name:
            raise ADBError("没有可用的 AVD，请先创建 Google Play 镜像的 AVD")

        serial = self.start_emulator(avd_name, headless=headless)
        report = self.check_device_health(serial)
        return serial, report
