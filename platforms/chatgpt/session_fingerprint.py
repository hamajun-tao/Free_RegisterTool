"""
Session 级指纹统一模块
在每次注册 session 初始化时锁定所有版本号参数，保证同一 session 全生命周期内指纹一致。

解决的问题：
- constants.py 和 request_header_enhancer.py 中版本号不一致
- 同一 session 里不同请求使用不同的 Chrome patch version
- 视口固定导致批量注册时所有 session 关联
- Accept-Language 与代理 IP 地理位置不匹配
"""

import random
import hashlib
import time
import json
import os
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field


# ============================================================================
# 常见视口分辨率池（按使用率加权）
# ============================================================================

COMMON_VIEWPORTS: List[Tuple[int, int]] = [
    (1920, 1080),   # 最常见
    (1366, 768),
    (1536, 864),
    (1440, 900),
    (1680, 1050),
    (1280, 720),
    (1600, 900),
    (1920, 1200),
    (2560, 1440),
]

# 分辨率与 device pixel ratio 的合理对应
_VIEWPORT_DPR_MAP = {
    (1920, 1080): [1.0, 1.25],
    (1366, 768): [1.0],
    (1536, 864): [1.25],
    (1440, 900): [1.0],
    (1680, 1050): [1.0],
    (1280, 720): [1.0, 1.5],
    (1600, 900): [1.0],
    (1920, 1200): [1.0],
    (2560, 1440): [1.0, 1.5],
}


# ============================================================================
# GeoIP → Accept-Language 映射
# ============================================================================

_GEO_LANGUAGE_MAP: Dict[str, List[str]] = {
    # 北美
    "US": ["en-US,en;q=0.9"],
    "CA": ["en-CA,en;q=0.9,fr-CA;q=0.8", "en-US,en;q=0.9"],
    "MX": ["es-MX,es;q=0.9,en;q=0.8"],
    # 欧洲
    "GB": ["en-GB,en;q=0.9"],
    "DE": ["de-DE,de;q=0.9,en;q=0.8"],
    "FR": ["fr-FR,fr;q=0.9,en;q=0.8"],
    "ES": ["es-ES,es;q=0.9,en;q=0.8"],
    "IT": ["it-IT,it;q=0.9,en;q=0.8"],
    "NL": ["nl-NL,nl;q=0.9,en;q=0.8"],
    "PT": ["pt-PT,pt;q=0.9,en;q=0.8"],
    "PL": ["pl-PL,pl;q=0.9,en;q=0.8"],
    "RU": ["ru-RU,ru;q=0.9,en;q=0.8"],
    "SE": ["sv-SE,sv;q=0.9,en;q=0.8"],
    "NO": ["nb-NO,nb;q=0.9,en;q=0.8"],
    "DK": ["da-DK,da;q=0.9,en;q=0.8"],
    "FI": ["fi-FI,fi;q=0.9,en;q=0.8"],
    "AT": ["de-AT,de;q=0.9,en;q=0.8"],
    "CH": ["de-CH,de;q=0.9,en;q=0.8,fr;q=0.7"],
    "BE": ["nl-BE,nl;q=0.9,fr;q=0.8,en;q=0.7"],
    "IE": ["en-IE,en;q=0.9"],
    "CZ": ["cs-CZ,cs;q=0.9,en;q=0.8"],
    "RO": ["ro-RO,ro;q=0.9,en;q=0.8"],
    "HU": ["hu-HU,hu;q=0.9,en;q=0.8"],
    "UA": ["uk-UA,uk;q=0.9,en;q=0.8"],
    "LV": ["lv-LV,lv;q=0.9,en;q=0.8"],
    "LT": ["lt-LT,lt;q=0.9,en;q=0.8"],
    "EE": ["et-EE,et;q=0.9,en;q=0.8"],
    # 亚洲
    "JP": ["ja-JP,ja;q=0.9,en;q=0.8"],
    "KR": ["ko-KR,ko;q=0.9,en;q=0.8"],
    "TW": ["zh-TW,zh;q=0.9,en;q=0.8"],
    "HK": ["zh-HK,zh;q=0.9,en;q=0.8"],
    "SG": ["en-SG,en;q=0.9,zh;q=0.8"],
    "IN": ["en-IN,en;q=0.9,hi;q=0.8"],
    "TH": ["th-TH,th;q=0.9,en;q=0.8"],
    "VN": ["vi-VN,vi;q=0.9,en;q=0.8"],
    "ID": ["id-ID,id;q=0.9,en;q=0.8"],
    "MY": ["ms-MY,ms;q=0.9,en;q=0.8"],
    "PH": ["en-PH,en;q=0.9,fil;q=0.8"],
    "TR": ["tr-TR,tr;q=0.9,en;q=0.8"],
    "IL": ["he-IL,he;q=0.9,en;q=0.8"],
    "AE": ["ar-AE,ar;q=0.9,en;q=0.8"],
    # 南美
    "BR": ["pt-BR,pt;q=0.9,en;q=0.8"],
    "AR": ["es-AR,es;q=0.9,en;q=0.8"],
    "CL": ["es-CL,es;q=0.9,en;q=0.8"],
    "CO": ["es-CO,es;q=0.9,en;q=0.8"],
    # 大洋洲
    "AU": ["en-AU,en;q=0.9"],
    "NZ": ["en-NZ,en;q=0.9"],
    # 非洲
    "ZA": ["en-ZA,en;q=0.9"],
    "NG": ["en-NG,en;q=0.9"],
    "EG": ["ar-EG,ar;q=0.9,en;q=0.8"],
}

# 默认语言（英语区）
_DEFAULT_ACCEPT_LANGUAGES = [
    "en-US,en;q=0.9",
    "en-US,en;q=0.9,en-GB;q=0.8",
]


# ============================================================================
# 指纹持久化支持
# ============================================================================

_FINGERPRINT_STORE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".fingerprint_cache",
)


def _ensure_store_dir() -> str:
    """确保指纹存储目录存在"""
    os.makedirs(_FINGERPRINT_STORE_DIR, exist_ok=True)
    return _FINGERPRINT_STORE_DIR


def _fingerprint_path(identity_key: str) -> str:
    """根据身份键生成持久化文件路径"""
    safe_key = hashlib.sha256(identity_key.encode()).hexdigest()[:16]
    return os.path.join(_ensure_store_dir(), f"fp_{safe_key}.json")


# ============================================================================
# SessionFingerprint 类
# ============================================================================

class SessionFingerprint:
    """
    Session 级别的指纹锁定器

    在 session 初始化时确定一次所有指纹参数，之后整个 session 全程复用。
    这确保了：
    - User-Agent 和 Sec-CH-UA 版本号在所有请求中保持一致
    - 视口在 Playwright 和协议层保持一致
    - Accept-Language 与代理 IP 的 GeoIP 匹配
    """

    def __init__(
        self,
        *,
        chrome_major: int = 136,
        geo_code: Optional[str] = None,
        identity_key: Optional[str] = None,
    ):
        """
        初始化 Session 指纹

        Args:
            chrome_major: Chrome 主版本号
            geo_code: 代理 IP 的国家/地区代码（如 "US", "JP"），用于 Accept-Language 匹配
            identity_key: 可选的身份键（如邮箱地址），用于指纹持久化跨 session 复用
        """
        # 尝试从持久化存储加载
        loaded = self._try_load(identity_key) if identity_key else None
        if loaded:
            self._apply_loaded(loaded)
            # 即使加载了持久化指纹，也要更新 geo_code 相关的 accept_language
            if geo_code and geo_code != loaded.get("geo_code"):
                self.geo_code = geo_code
                self.accept_language = self._resolve_accept_language(geo_code)
            return

        # ---- 锁定 Chrome 版本号 ----
        self.chrome_major = chrome_major
        self.chrome_patch = random.randint(7103, 7110)
        self.chrome_build = random.randint(80, 150)
        self.chrome_version = f"{chrome_major}.0.{self.chrome_patch}.{self.chrome_build}"

        # ---- 锁定 User-Agent ----
        self.user_agent = (
            f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            f"AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{self.chrome_version} Safari/537.36"
        )

        # ---- 锁定 Sec-CH-UA 系列 ----
        self.sec_ch_ua = (
            f'"Chromium";v="{chrome_major}", '
            f'"Google Chrome";v="{chrome_major}", '
            f'"Not.A/Brand";v="99"'
        )
        self.sec_ch_ua_full_version = f'"{self.chrome_version}"'
        self.sec_ch_ua_full_version_list = (
            f'"Chromium";v="{self.chrome_version}", '
            f'"Google Chrome";v="{self.chrome_version}", '
            f'"Not.A/Brand";v="99.0.0.0"'
        )

        # ---- 锁定视口 ----
        viewport = random.choice(COMMON_VIEWPORTS)
        self.viewport_width = viewport[0]
        self.viewport_height = viewport[1]
        dpr_candidates = _VIEWPORT_DPR_MAP.get(viewport, [1.0])
        self.device_pixel_ratio = random.choice(dpr_candidates)

        # ---- 锁定 screen 信息（与视口对应）----
        self.screen_width = self.viewport_width
        self.screen_height = self.viewport_height
        self.avail_width = self.viewport_width - random.randint(0, 8)
        self.avail_height = self.viewport_height - random.randint(40, 80)

        # ---- 锁定 Accept-Language ----
        self.geo_code = geo_code
        self.accept_language = self._resolve_accept_language(geo_code)

        # ---- 锁定硬件信息 ----
        self.hardware_concurrency = random.choice([4, 6, 8, 12, 16])
        self.device_memory = random.choice([4, 8, 16])
        self.platform = "Win32"

        # ---- 锁定 platform version ----
        self.platform_version = f'"{random.randint(10, 15)}.0.0"'

        # ---- 身份键（用于持久化）----
        self._identity_key = identity_key

        # 持久化保存
        if identity_key:
            self._save(identity_key)

    @staticmethod
    def _resolve_accept_language(geo_code: Optional[str]) -> str:
        """根据 GeoIP 代码解析对应的 Accept-Language"""
        if not geo_code:
            return random.choice(_DEFAULT_ACCEPT_LANGUAGES)
        candidates = _GEO_LANGUAGE_MAP.get(geo_code.upper())
        if candidates:
            return random.choice(candidates)
        return random.choice(_DEFAULT_ACCEPT_LANGUAGES)

    def get_headers_patch(self) -> Dict[str, str]:
        """
        返回可直接 update 到 headers 字典的指纹补丁

        使用方法：
            headers = {...existing headers...}
            headers.update(session_fp.get_headers_patch())
        """
        return {
            "user-agent": self.user_agent,
            "accept-language": self.accept_language,
            "sec-ch-ua": self.sec_ch_ua,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-ch-ua-platform-version": self.platform_version,
            "sec-ch-ua-full-version": self.sec_ch_ua_full_version,
            "sec-ch-ua-full-version-list": self.sec_ch_ua_full_version_list,
            "sec-ch-ua-arch": '"x86"',
            "sec-ch-ua-bitness": '"64"',
        }

    def get_viewport(self) -> Dict[str, int]:
        """返回 Playwright 兼容的 viewport 配置"""
        return {
            "width": self.viewport_width,
            "height": self.viewport_height,
        }

    def get_screen_info(self) -> Dict[str, any]:
        """返回 screen 信息，用于 Playwright init_script 注入"""
        return {
            "width": self.screen_width,
            "height": self.screen_height,
            "availWidth": self.avail_width,
            "availHeight": self.avail_height,
            "colorDepth": 24,
            "pixelDepth": 24,
            "devicePixelRatio": self.device_pixel_ratio,
        }

    def to_dict(self) -> Dict:
        """序列化为字典（用于持久化）"""
        return {
            "chrome_major": self.chrome_major,
            "chrome_patch": self.chrome_patch,
            "chrome_build": self.chrome_build,
            "chrome_version": self.chrome_version,
            "user_agent": self.user_agent,
            "sec_ch_ua": self.sec_ch_ua,
            "sec_ch_ua_full_version": self.sec_ch_ua_full_version,
            "sec_ch_ua_full_version_list": self.sec_ch_ua_full_version_list,
            "viewport_width": self.viewport_width,
            "viewport_height": self.viewport_height,
            "device_pixel_ratio": self.device_pixel_ratio,
            "screen_width": self.screen_width,
            "screen_height": self.screen_height,
            "avail_width": self.avail_width,
            "avail_height": self.avail_height,
            "geo_code": self.geo_code,
            "accept_language": self.accept_language,
            "hardware_concurrency": self.hardware_concurrency,
            "device_memory": self.device_memory,
            "platform": self.platform,
            "platform_version": self.platform_version,
            "created_at": time.time(),
        }

    def _save(self, identity_key: str) -> None:
        """持久化指纹到文件"""
        try:
            fp_path = _fingerprint_path(identity_key)
            with open(fp_path, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception:
            pass  # 持久化失败不影响正常运行

    @staticmethod
    def _try_load(identity_key: str) -> Optional[Dict]:
        """尝试从文件加载持久化指纹"""
        try:
            fp_path = _fingerprint_path(identity_key)
            if not os.path.exists(fp_path):
                return None
            with open(fp_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 检查是否过期（30天）
            created_at = data.get("created_at", 0)
            if time.time() - created_at > 30 * 86400:
                os.remove(fp_path)
                return None
            return data
        except Exception:
            return None

    def _apply_loaded(self, data: Dict) -> None:
        """从加载的字典恢复指纹"""
        self.chrome_major = data.get("chrome_major", 136)
        self.chrome_patch = data.get("chrome_patch", 7103)
        self.chrome_build = data.get("chrome_build", 92)
        self.chrome_version = data.get("chrome_version", "136.0.7103.92")
        self.user_agent = data.get("user_agent", "")
        self.sec_ch_ua = data.get("sec_ch_ua", "")
        self.sec_ch_ua_full_version = data.get("sec_ch_ua_full_version", "")
        self.sec_ch_ua_full_version_list = data.get("sec_ch_ua_full_version_list", "")
        self.viewport_width = data.get("viewport_width", 1440)
        self.viewport_height = data.get("viewport_height", 900)
        self.device_pixel_ratio = data.get("device_pixel_ratio", 1.0)
        self.screen_width = data.get("screen_width", self.viewport_width)
        self.screen_height = data.get("screen_height", self.viewport_height)
        self.avail_width = data.get("avail_width", self.viewport_width)
        self.avail_height = data.get("avail_height", self.viewport_height - 40)
        self.geo_code = data.get("geo_code")
        self.accept_language = data.get("accept_language", "en-US,en;q=0.9")
        self.hardware_concurrency = data.get("hardware_concurrency", 8)
        self.device_memory = data.get("device_memory", 8)
        self.platform = data.get("platform", "Win32")
        self.platform_version = data.get("platform_version", '"15.0.0"')
        self._identity_key = None

    def __repr__(self) -> str:
        return (
            f"SessionFingerprint("
            f"chrome={self.chrome_version}, "
            f"viewport={self.viewport_width}x{self.viewport_height}, "
            f"lang={self.accept_language[:10]}..., "
            f"geo={self.geo_code or 'N/A'}"
            f")"
        )
