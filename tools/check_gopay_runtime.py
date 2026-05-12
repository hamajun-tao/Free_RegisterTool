"""检查 GoPay 执行所需的运行时依赖：WhatsApp Relay + Android Emulator"""
import os
import sys
import subprocess
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# -------- 1. WhatsApp Relay 状态 --------
print("=" * 60)
print("[1] WhatsApp Relay 状态")
print("=" * 60)
try:
    from services import wa_relay_manager as wa  # type: ignore
    info = wa.get_status()
    for k, v in info.items():
        print(f"  {k} = {v}")
    otp_file = wa.get_otp_file_path()
    print(f"  otp_file_path = {otp_file}")
    print(f"  is_running = {wa.is_running()}")
    print(f"  is_logged_in = {wa.is_logged_in()}")
    print(f"  otp_file_exists = {os.path.exists(otp_file)}")
    if os.path.exists(otp_file):
        try:
            with open(otp_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
            print(f"  otp_file_content = {content!r}")
        except Exception as e:
            print(f"  otp_file_read_error = {e}")
except Exception as e:
    print(f"  ERROR: {e}")

# -------- 2. Android Emulator --------
print("\n" + "=" * 60)
print("[2] Android Emulator / ADB")
print("=" * 60)
# adb devices
adb_path = "adb"
for candidate in ("adb", os.environ.get("ANDROID_HOME", "") + "/platform-tools/adb.exe"):
    if not candidate:
        continue
    try:
        r = subprocess.run([candidate, "version"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            adb_path = candidate
            print(f"  adb = {candidate}")
            print(f"  version_line = {r.stdout.splitlines()[0] if r.stdout else ''}")
            break
    except Exception:
        continue
else:
    print("  ERROR: adb 未找到")

try:
    r = subprocess.run([adb_path, "devices"], capture_output=True, text=True, timeout=8)
    print("  --- adb devices ---")
    for line in (r.stdout or "").splitlines():
        if line.strip():
            print(f"    {line}")
except Exception as e:
    print(f"  ERROR: adb devices: {e}")

# avd list
print("\n  --- AVD 列表 ---")
emu_paths = []
ah = os.environ.get("ANDROID_HOME", "")
if ah:
    emu_paths.append(os.path.join(ah, "emulator", "emulator.exe"))
    emu_paths.append(os.path.join(ah, "emulator", "emulator"))
emu_paths.append("emulator")
for ep in emu_paths:
    try:
        r = subprocess.run([ep, "-list-avds"], capture_output=True, text=True, timeout=8)
        if r.returncode == 0:
            print(f"  emulator = {ep}")
            for line in (r.stdout or "").splitlines():
                if line.strip():
                    print(f"    AVD: {line.strip()}")
            break
    except Exception:
        continue
else:
    print("  ERROR: 找不到 emulator 命令")
