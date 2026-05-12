"""Playwright helpers for Sentinel, Cloudflare warmup, and browser-native form submits."""

from __future__ import annotations

import json
from typing import Callable, Optional, TYPE_CHECKING
from urllib.parse import urlparse

from core.proxy_utils import build_playwright_proxy_config
from .browser_fingerprint_enhancer import BrowserFingerprintGenerator
from .utils import extract_flow_state, normalize_flow_url

if TYPE_CHECKING:
    from .session_fingerprint import SessionFingerprint


# ============================================================================
# Playwright 反自动化检测脚本
# 在每个 context 创建时注入，消除常见的 bot 检测特征
# ============================================================================

_STEALTH_INIT_SCRIPT = """
(() => {
    // 1. 移除 navigator.webdriver 标记
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    try { delete navigator.__proto__.webdriver; } catch (_e) {}

    // 2. 补全 Chrome 运行时对象（Playwright 默认不提供）
    if (!window.chrome) {
        window.chrome = {
            runtime: {
                connect: function() {},
                sendMessage: function() {},
            },
            loadTimes: function() {
                return {
                    commitLoadTime: Date.now() / 1000,
                    connectionInfo: 'h2',
                    finishDocumentLoadTime: Date.now() / 1000 + 0.1,
                    finishLoadTime: Date.now() / 1000 + 0.2,
                    firstPaintAfterLoadTime: 0,
                    firstPaintTime: Date.now() / 1000 + 0.05,
                    navigationType: 'Other',
                    npnNegotiatedProtocol: 'h2',
                    requestTime: Date.now() / 1000 - 0.5,
                    startLoadTime: Date.now() / 1000 - 0.4,
                    wasAlternateProtocolAvailable: false,
                    wasFetchedViaSpdy: true,
                    wasNpnNegotiated: true,
                };
            },
            csi: function() {
                return {
                    onloadT: Date.now(),
                    pageT: Date.now() / 1000,
                    startE: Date.now(),
                    tran: 15,
                };
            },
        };
    }

    // 3. 修复 permissions API（Playwright 可能返回异常）
    try {
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission })
                : originalQuery(parameters)
        );
    } catch (_e) {}

    // 4. 伪装 plugins 和 mimeTypes（空列表是明显的自动化特征）
    Object.defineProperty(navigator, 'plugins', {
        get: () => {
            const pluginData = [
                { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
            ];
            const plugins = Object.create(PluginArray.prototype);
            pluginData.forEach((p, i) => {
                const plugin = Object.create(Plugin.prototype);
                Object.defineProperties(plugin, {
                    name: { value: p.name },
                    filename: { value: p.filename },
                    description: { value: p.description },
                    length: { value: 0 },
                });
                Object.defineProperty(plugins, i, { value: plugin });
            });
            Object.defineProperty(plugins, 'length', { value: pluginData.length });
            return plugins;
        },
    });

    // 5. 确保 languages 属性正确
    Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en'],
    });

    // 6. 修复 Connection API
    if (!navigator.connection) {
        Object.defineProperty(navigator, 'connection', {
            get: () => ({
                effectiveType: '4g',
                rtt: 50,
                downlink: 10,
                saveData: false,
            }),
        });
    }

    // 7. 隐藏 CDP (Chrome DevTools Protocol) 检测
    const originalGetOwnPropertyDescriptor = Object.getOwnPropertyDescriptor;
    Object.getOwnPropertyDescriptor = function(obj, prop) {
        if (prop === 'domAutomation' || prop === 'domAutomationController') {
            return undefined;
        }
        return originalGetOwnPropertyDescriptor.apply(this, arguments);
    };
})();
"""


def _flow_page_url(flow: str) -> str:
    flow_name = str(flow or "").strip().lower()
    mapping = {
        "authorize_continue": "https://auth.openai.com/create-account",
        "username_password_create": "https://auth.openai.com/create-account/password",
        "password_verify": "https://auth.openai.com/log-in/password",
        "email_otp_validate": "https://auth.openai.com/email-verification",
        "oauth_create_account": "https://auth.openai.com/about-you",
    }
    return mapping.get(flow_name, "https://auth.openai.com/about-you")


def _normalize_storage_state(storage_state: Optional[dict]) -> dict:
    normalized: dict[str, dict[str, dict[str, str]]] = {}
    raw = storage_state if isinstance(storage_state, dict) else {}
    for origin, payload in raw.items():
        origin_key = str(origin or "").strip()
        if not origin_key:
            continue
        data = payload if isinstance(payload, dict) else {}
        local_storage = data.get("localStorage") if isinstance(data.get("localStorage"), dict) else {}
        session_storage = data.get("sessionStorage") if isinstance(data.get("sessionStorage"), dict) else {}
        normalized[origin_key] = {
            "localStorage": {
                str(key): "" if value is None else str(value)
                for key, value in local_storage.items()
                if str(key or "").strip()
            },
            "sessionStorage": {
                str(key): "" if value is None else str(value)
                for key, value in session_storage.items()
                if str(key or "").strip()
            },
        }
    return normalized


def _build_storage_seed_script(storage_state: Optional[dict]) -> Optional[str]:
    normalized = _normalize_storage_state(storage_state)
    if not normalized:
        return None
    snapshot = json.dumps(normalized, ensure_ascii=True)
    return f"""
(() => {{
  const snapshot = {snapshot};
  const current = snapshot[window.location.origin] || snapshot["https://auth.openai.com"] || {{}};
  const apply = (storage, values) => {{
    if (!values || typeof values !== "object") return;
    for (const [key, value] of Object.entries(values)) {{
      try {{
        storage.setItem(String(key), value == null ? "" : String(value));
      }} catch (_err) {{}}
    }}
  }};
  try {{ apply(window.localStorage, current.localStorage); }} catch (_err) {{}}
  try {{ apply(window.sessionStorage, current.sessionStorage); }} catch (_err) {{}}
}})();
""".strip()


def _capture_storage_state(page) -> dict:
    try:
        result = page.evaluate(
            """
            () => {
                const dump = (storage) => {
                    const out = {};
                    for (let i = 0; i < storage.length; i += 1) {
                        const key = storage.key(i);
                        if (key != null) {
                            out[key] = storage.getItem(key);
                        }
                    }
                    return out;
                };
                return {
                    [window.location.origin]: {
                        localStorage: dump(window.localStorage),
                        sessionStorage: dump(window.sessionStorage),
                    },
                };
            }
            """
        )
    except Exception:
        result = {}
    return _normalize_storage_state(result)


def _extract_browser_flow_metadata(body_text: str, current_url: str, fallback_page_type: str = "") -> dict:
    try:
        response_data = json.loads(body_text or "{}") if body_text else {}
    except Exception:
        response_data = {}

    flow_state = extract_flow_state(
        response_data,
        current_url=current_url,
        auth_base="https://auth.openai.com",
    )
    continue_url = flow_state.continue_url or normalize_flow_url(
        str(response_data.get("continue_url") or ""),
        auth_base="https://auth.openai.com",
    )
    page_type = flow_state.page_type or str(fallback_page_type or "").strip()
    final_url = normalize_flow_url(
        flow_state.current_url or continue_url or current_url,
        auth_base="https://auth.openai.com",
    )
    return {
        "response_data": response_data,
        "page_type": page_type,
        "continue_url": continue_url,
        "final_url": final_url,
    }


def _build_account_api_request(form_type: str, form_value: str, current_url: str) -> dict:
    form_type_norm = str(form_type or "").strip().lower()
    request = {
        "endpoint": "",
        "body": None,
        "method": "POST",
        "sentinel_header_name": "openai-sentinel-token",
    }

    if form_type_norm == "email":
        url_low = str(current_url or "").lower()
        screen_hint = "login" if ("log-in" in url_low or "/login" in url_low) else "signup"
        request["endpoint"] = "https://auth.openai.com/api/accounts/authorize/continue"
        request["body"] = json.dumps(
            {
                "username": {"value": form_value, "kind": "email"},
                "screen_hint": screen_hint,
            }
        )
    elif form_type_norm == "password":
        request["endpoint"] = "https://auth.openai.com/api/accounts/user/register"
        request["body"] = json.dumps({"password": form_value})
    elif form_type_norm == "login_password":
        request["endpoint"] = "https://auth.openai.com/api/accounts/password/verify"
        request["body"] = json.dumps({"password": form_value})
    elif form_type_norm == "otp_send":
        request["endpoint"] = form_value or "https://auth.openai.com/api/accounts/email-otp/send"
        request["method"] = "GET"
    elif form_type_norm == "otp_validate":
        request["endpoint"] = "https://auth.openai.com/api/accounts/email-otp/validate"
        request["body"] = json.dumps({"code": form_value})
    elif form_type_norm == "workspace_list":
        request["endpoint"] = "https://auth.openai.com/api/accounts/workspace/"
        request["method"] = "GET"
    elif form_type_norm == "workspace_select":
        request["endpoint"] = "https://auth.openai.com/api/accounts/workspace/select"
        request["body"] = str(form_value or "{}")
    elif form_type_norm == "organization_select":
        request["endpoint"] = "https://auth.openai.com/api/accounts/organization/select"
        request["body"] = str(form_value or "{}")
    elif form_type_norm == "create_account":
        request["endpoint"] = "https://auth.openai.com/api/accounts/create_account"
        request["body"] = str(form_value or "")
    elif form_type_norm == "phone_send":
        request["endpoint"] = "https://auth.openai.com/api/accounts/add-phone/send"
        request["body"] = json.dumps({"phone_number": form_value})
    elif form_type_norm == "phone_validate":
        request["endpoint"] = "https://auth.openai.com/api/accounts/phone-otp/validate"
        request["body"] = json.dumps({"code": form_value})
    elif form_type_norm == "phone_otp_resend":
        request["endpoint"] = "https://auth.openai.com/api/accounts/phone-otp/resend"
        request["body"] = str(form_value or "{}")

    return request


def _build_account_api_headers(
    *,
    current_url: str,
    sentinel_token: Optional[str],
    sentinel_header_name: str,
    device_id: Optional[str],
    session_fp: Optional["SessionFingerprint"] = None,
) -> dict:
    # 优先从 SessionFingerprint 获取真实一致的 headers
    if session_fp:
        _sec_ch_ua = getattr(session_fp, 'sec_ch_ua', '') or '"Chromium";v="136", "Google Chrome";v="136", "Not=A?Brand";v="99"'
        _accept_lang = getattr(session_fp, 'accept_language', '') or "en-US,en;q=0.9"
    else:
        _sec_ch_ua = '"Chromium";v="136", "Google Chrome";v="136", "Not=A?Brand";v="99"'
        _accept_lang = "en-US,en;q=0.9"
    headers = {
        "content-type": "application/json",
        "accept": "application/json",
        "referer": current_url,
        "origin": "https://auth.openai.com",
        # Chrome client hints（反自动化必须，与 UA 版本一致）
        "sec-ch-ua": _sec_ch_ua,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        # Fetch metadata（标识请求来源上下文）
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        # accept-language 与代理 geo 一致
        "accept-language": _accept_lang,
    }
    if device_id:
        headers["oai-device-id"] = str(device_id)
    if sentinel_token:
        headers[sentinel_header_name] = sentinel_token
    return headers


def _safe_endpoint_path(endpoint: str) -> str:
    try:
        parsed = urlparse(str(endpoint or ""))
        return parsed.path or str(endpoint or "")
    except Exception:
        return str(endpoint or "")


def _new_context(
    browser,
    *,
    device_id: Optional[str],
    storage_state: Optional[dict] = None,
    session_fp: Optional["SessionFingerprint"] = None,
):
    # 视口和 UA：优先使用 SessionFingerprint 锁定值，否则使用默认值
    if session_fp:
        viewport = session_fp.get_viewport()
        user_agent = session_fp.user_agent
    else:
        viewport = {"width": 1440, "height": 900}
        user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/136.0.7103.92 Safari/537.36"
        )

    # extra_http_headers 确保 context.request.* 也携带真实浏览器 headers
    _extra_headers = {}
    if session_fp:
        _extra_headers["accept-language"] = getattr(session_fp, 'accept_language', '') or "en-US,en;q=0.9"
        _extra_headers["sec-ch-ua"] = getattr(session_fp, 'sec_ch_ua', '') or '"Chromium";v="136", "Google Chrome";v="136", "Not=A?Brand";v="99"'
        _extra_headers["sec-ch-ua-mobile"] = "?0"
        _extra_headers["sec-ch-ua-platform"] = '"Windows"'
    context = browser.new_context(
        viewport=viewport,
        user_agent=user_agent,
        ignore_https_errors=True,
        extra_http_headers=_extra_headers if _extra_headers else None,
    )

    # 注入反自动化检测脚本
    try:
        context.add_init_script(_STEALTH_INIT_SCRIPT)
    except Exception:
        pass

    # 注入 screen/navigator 属性覆盖脚本（与视口保持一致）
    if session_fp:
        screen_info = session_fp.get_screen_info()
        advanced_fp = BrowserFingerprintGenerator().generate()  # 获取高级 Canvas/WebGL 等指纹
        
        # 将高级指纹打包到前端以便 JS 使用
        canvas_hash = advanced_fp.get("canvas", {}).get("hash", "")
        webgl_vendor = advanced_fp.get("webgl", {}).get("vendor", "Google Inc. (NVIDIA)")
        webgl_renderer = advanced_fp.get("webgl", {}).get("renderer", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0)")
        
        # 从 accept_language 解析 navigator.languages 列表
        _accept_lang_str = getattr(session_fp, 'accept_language', '') or "en-US,en;q=0.9"
        _nav_languages = []
        for _part in _accept_lang_str.split(","):
            _lang = _part.split(";")[0].strip()
            if _lang:
                _nav_languages.append(_lang)
        if not _nav_languages:
            _nav_languages = ["en-US", "en"]
        _nav_languages_js = str(_nav_languages)  # Python list → JS array syntax

        screen_override_script = f"""
(() => {{
    // 覆盖 screen 属性使其与 viewport 一致
    Object.defineProperty(screen, 'width', {{ get: () => {screen_info['width']} }});
    Object.defineProperty(screen, 'height', {{ get: () => {screen_info['height']} }});
    Object.defineProperty(screen, 'availWidth', {{ get: () => {screen_info['availWidth']} }});
    Object.defineProperty(screen, 'availHeight', {{ get: () => {screen_info['availHeight']} }});
    Object.defineProperty(screen, 'colorDepth', {{ get: () => {screen_info['colorDepth']} }});
    Object.defineProperty(screen, 'pixelDepth', {{ get: () => {screen_info['pixelDepth']} }});
    Object.defineProperty(window, 'devicePixelRatio', {{ get: () => {screen_info['devicePixelRatio']} }});
    
    // 覆盖 navigator 硬件信息
    Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {session_fp.hardware_concurrency} }});
    Object.defineProperty(navigator, 'deviceMemory', {{ get: () => {session_fp.device_memory} }});
    Object.defineProperty(navigator, 'platform', {{ get: () => '{session_fp.platform}' }});

    // navigator.languages 必须与 accept-language 一致（反检测关键）
    Object.defineProperty(navigator, 'languages', {{ get: () => {_nav_languages_js} }});
    Object.defineProperty(navigator, 'language', {{ get: () => '{_nav_languages[0]}' }});
    
    // WebGL 覆盖
    const getParameterProxy = function(parameter) {{
        // 37445 = UNMASKED_VENDOR_WEBGL
        if (parameter === 37445) return "{webgl_vendor}";
        // 37446 = UNMASKED_RENDERER_WEBGL
        if (parameter === 37446) return "{webgl_renderer}";
        // eslint-disable-next-line no-undef
        return Reflect.apply(Object.getOwnPropertyDescriptor(WebGLRenderingContext.prototype, 'getParameter').value, this, [parameter]);
    }};
    
    try {{
        const nativeGetParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {{
            if (parameter === 37445) return "{webgl_vendor}";
            if (parameter === 37446) return "{webgl_renderer}";
            return nativeGetParameter.call(this, parameter);
        }};
        const nativeGetParameter2 = WebGL2RenderingContext.prototype.getParameter;
        WebGL2RenderingContext.prototype.getParameter = function(parameter) {{
            if (parameter === 37445) return "{webgl_vendor}";
            if (parameter === 37446) return "{webgl_renderer}";
            return nativeGetParameter2.call(this, parameter);
        }};
    }} catch(e) {{}}

    // Canvas 噪声注入（轻量噪声）
    try {{
        const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL = function() {{
            const ctx = this.getContext('2d');
            if (ctx) {{
                const shift = Math.random() * 0.5;
                ctx.fillStyle = `rgba(255, 255, 255, ${{shift}})`;
                ctx.fillText('{canvas_hash}'.substring(0, 5), 0, 0);
            }}
            return originalToDataURL.apply(this, arguments);
        }};
    }} catch(e) {{}}

    // WebRTC 暴露防止（禁用 WebRTC P2P）
    if (window.RTCPeerConnection) {{
        const originalRTC = window.RTCPeerConnection;
        window.RTCPeerConnection = function(...args) {{
            const pc = new originalRTC(...args);
            pc.createDataChannel = () => ({{ close: () => {{}}, send: () => {{}} }});
            // eslint-disable-next-line no-undef
            return pc;
        }};
    }}
}})();
"""
        try:
            context.add_init_script(screen_override_script)
        except Exception:
            pass

    storage_seed_script = _build_storage_seed_script(storage_state)
    if storage_seed_script:
        try:
            context.add_init_script(storage_seed_script)
        except Exception:
            pass

    if device_id:
        try:
            context.add_cookies(
                [
                    {
                        "name": "oai-did",
                        "value": str(device_id),
                        "url": "https://auth.openai.com/",
                        "path": "/",
                        "secure": True,
                        "sameSite": "Lax",
                    }
                ]
            )
        except Exception:
            pass
    return context


def _inject_initial_cookies(context, initial_cookies: Optional[list[dict]], logger: Callable[[str], None]) -> None:
    if not initial_cookies:
        return

    injected = 0
    for cookie in initial_cookies:
        try:
            name = str(cookie.get("name") or "").strip()
            if not name:
                continue
            value = str(cookie.get("value") or "")
            cookie_path = str(cookie.get("path") or "/")
            if name.startswith("__Host-"):
                context.add_cookies(
                    [
                        {
                            "name": name,
                            "value": value,
                            "url": "https://auth.openai.com/",
                            "path": "/",
                        }
                    ]
                )
            else:
                context.add_cookies(
                    [
                        {
                            "name": name,
                            "value": value,
                            "domain": str(cookie.get("domain") or ".openai.com"),
                            "path": cookie_path,
                        }
                    ]
                )
            injected += 1
        except Exception:
            pass

    if injected:
        logger(f"Browser Form: injected {injected} initial cookies")


def _record_navigation(page) -> list[str]:
    navigation_chain: list[str] = []

    def _remember(frame):
        try:
            if frame != page.main_frame:
                return
            url = str(frame.url or "").strip()
            if url and (not navigation_chain or navigation_chain[-1] != url):
                navigation_chain.append(url)
        except Exception:
            pass

    try:
        page.on("framenavigated", _remember)
    except Exception:
        pass
    return navigation_chain


def _extract_cookies(context) -> list[dict]:
    raw_cookies = context.cookies()
    return [
        {
            "name": cookie.get("name", ""),
            "value": cookie.get("value", ""),
            "domain": cookie.get("domain", ""),
            "path": cookie.get("path", "/"),
        }
        for cookie in raw_cookies
        if cookie.get("name")
    ]


def get_sentinel_token_via_browser(
    *,
    flow: str,
    proxy: Optional[str] = None,
    timeout_ms: int = 45000,
    page_url: Optional[str] = None,
    headless: bool = True,
    device_id: Optional[str] = None,
    log_fn: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    result = _run_browser_for_page(
        flow=flow,
        proxy=proxy,
        timeout_ms=timeout_ms,
        page_url=page_url,
        headless=headless,
        device_id=device_id,
        log_fn=log_fn,
        extract_cookies=False,
    )
    return result.get("sentinel_token") if result else None


def warmup_page_and_extract_cookies(
    *,
    page_url: str,
    proxy: Optional[str] = None,
    timeout_ms: int = 30000,
    headless: bool = True,
    device_id: Optional[str] = None,
    log_fn: Optional[Callable[[str], None]] = None,
    session_fp: Optional["SessionFingerprint"] = None,
) -> dict[str, str]:
    result = _run_browser_for_page(
        flow="",
        proxy=proxy,
        timeout_ms=timeout_ms,
        page_url=page_url,
        headless=headless,
        device_id=device_id,
        log_fn=log_fn,
        extract_cookies=True,
        skip_sentinel=True,
        session_fp=session_fp,
    )
    if not result:
        return {}
    raw = result.get("cookies", []) or []
    return {cookie.get("name", ""): cookie.get("value", "") for cookie in raw if cookie.get("name")}


def browser_form_submit(
    *,
    page_url: str,
    form_type: str,
    form_value: str = "",
    proxy: Optional[str] = None,
    timeout_ms: int = 45000,
    headless: bool = True,
    device_id: Optional[str] = None,
    log_fn: Optional[Callable[[str], None]] = None,
    flow: str = "",
    initial_cookies: Optional[list[dict]] = None,
    initial_storage_state: Optional[dict] = None,
    session_fp: Optional["SessionFingerprint"] = None,
) -> Optional[dict]:
    logger = log_fn or (lambda _msg: None)
    flow_name = str(flow or "").strip().lower()
    form_type_norm = str(form_type or "").strip().lower()

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        logger(f"Browser Form unavailable: {exc}")
        return None

    launch_args = {
        "headless": bool(headless),
        "args": [
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
        ],
    }
    proxy_config = build_playwright_proxy_config(proxy)
    if proxy_config:
        launch_args["proxy"] = proxy_config

    logger(
        f"Browser Form: type={form_type_norm}, url={page_url[:100]}"
        + (f", flow={flow_name}" if flow_name else "")
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**launch_args)
        try:
            context = _new_context(
                browser,
                device_id=device_id,
                storage_state=initial_storage_state,
                session_fp=session_fp,
            )
            _inject_initial_cookies(context, initial_cookies, logger)
            page = context.new_page()
            navigation_chain = _record_navigation(page)

            logger("Browser Form: loading page and waiting for Cloudflare...")
            initial_response = page.goto(page_url, wait_until="domcontentloaded", timeout=timeout_ms)
            challenge_passed = False
            try:
                page.wait_for_function(
                    """() => {
                        const title = document.title || '';
                        const body = document.body ? document.body.innerText || '' : '';
                        return !title.includes('Just a moment') && !title.includes('Attention Required')
                            && !body.includes('Checking your browser') && !body.includes('cf-browser-verification');
                    }""",
                    timeout=min(timeout_ms, 20000),
                )
                challenge_passed = True
                logger("Browser Form: Cloudflare challenge passed")
            except Exception:
                logger("Browser Form: Cloudflare wait timed out, continuing")

            from .human_behavior_simulator import HumanBehaviorSimulator
            # 使用行为模拟器带来真实的页面浏览停顿，代替写死的 1500ms
            HumanBehaviorSimulator().page_load_observation()

            sentinel_result = None
            if flow_name:
                try:
                    page.wait_for_function(
                        "() => typeof window.SentinelSDK !== 'undefined' && typeof window.SentinelSDK.token === 'function'",
                        timeout=min(timeout_ms, 30000),
                    )
                    sentinel_result = page.evaluate(
                        """
                        async ({ flow }) => {
                            try {
                                const token = await window.SentinelSDK.token(flow);
                                return { success: true, token };
                            } catch (e) {
                                return {
                                    success: false,
                                    error: (e && (e.message || String(e))) || "unknown",
                                };
                            }
                        }
                        """,
                        {"flow": flow_name},
                    )
                    if sentinel_result and sentinel_result.get("success") and sentinel_result.get("token"):
                        logger(f"Browser Form: Sentinel token acquired ({flow_name})")
                except Exception as exc:
                    logger(f"Browser Form: Sentinel token failed ({flow_name}): {exc}")

            sentinel_token = (
                sentinel_result.get("token")
                if sentinel_result and sentinel_result.get("success")
                else None
            )
            current_url = str(page.url or page_url or "").strip()
            # ⭐ 新增：consent_authorize - 点击 OAuth consent 页面的 "Authorize" 按钮
            # 用于个人账号（无 workspace）直接从 consent 重定向到 callback URL
            if form_type_norm == "consent_authorize":
                # 已经 load 了 consent URL；现在找 Authorize 按钮并点击
                logger("Browser Form: consent_authorize - 等待 React 渲染...")
                clicked = False
                # 等待 React hydration 完成（consent 页面是 SPA）
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                try:
                    page.wait_for_selector("button", state="visible", timeout=5000)
                except Exception:
                    pass
                logger("Browser Form: consent_authorize - 查找 Authorize/Allow 按钮...")
                button_selectors = [
                    'button:has-text("Authorize")',
                    'button:has-text("Allow")',
                    'button:has-text("Continue")',
                    'button:has-text("Sign in")',
                    'button:has-text("Accept")',
                    'button:has-text("Confirm")',
                    'button:has-text("Log in")',
                    'button[data-testid="consent-approve"]',
                    'button[data-testid="approve-button"]',
                    'button[type="submit"]',
                    '[role="button"]:has-text("Authorize")',
                    '[role="button"]:has-text("Allow")',
                    '[role="button"]:has-text("Sign in")',
                    'a:has-text("Authorize")',
                    'a:has-text("Allow")',
                ]
                for sel in button_selectors:
                    try:
                        btn = page.locator(sel).first
                        if btn and btn.count() > 0:
                            logger(f"Browser Form: found button via selector: {sel}")
                            # 等待可点击
                            btn.wait_for(state="visible", timeout=5000)
                            # 并行等 navigation + 点击
                            try:
                                with page.expect_navigation(timeout=15000, wait_until="commit"):
                                    btn.click(timeout=5000)
                            except Exception:
                                # 某些 consent 页面是 XHR submit，不会整页 navigate
                                # 尝试直接等 URL 变化
                                try:
                                    btn.click(timeout=5000)
                                except Exception:
                                    pass
                                try:
                                    page.wait_for_url(
                                        lambda url: "callback" in url or "chatgpt.com" in url or "localhost" in url,
                                        timeout=10000,
                                    )
                                except Exception:
                                    pass
                            clicked = True
                            break
                    except Exception as exc:
                        logger(f"Browser Form: selector {sel} failed: {exc}")
                        continue
                # 兜底：如果命名选择器都失败，尝试点击第一个可见的 primary/submit 按钮
                # 但需要排除"重试/Retry/Try again"等错误页面按钮
                error_only_button_texts = {
                    "重试", "重 试", "retry", "try again", "刷新", "reload",
                }
                if not clicked:
                    try:
                        all_buttons = page.locator("button:visible")
                        btn_count = all_buttons.count()
                        logger(f"Browser Form: consent_authorize fallback - 页面共 {btn_count} 个可见按钮")
                        button_texts = []
                        for bi in range(min(btn_count, 5)):
                            btn_text = (all_buttons.nth(bi).text_content() or "").strip()
                            button_texts.append(btn_text)
                            logger(f"Browser Form:   button[{bi}]: '{btn_text}'")

                        # 检测错误页面：所有按钮都是"重试/Retry"等错误恢复按钮
                        normalized_texts = [t.strip().lower() for t in button_texts if t.strip()]
                        if normalized_texts and all(
                            any(err_kw in t for err_kw in error_only_button_texts)
                            for t in normalized_texts
                        ):
                            logger(
                                "Browser Form: consent_authorize - 检测到错误页面（仅含重试按钮），"
                                "跳过点击。OAuth consent 已失败，可能原因：account 未通过 OpenAI 风控/"
                                "需先验证手机号/codex 应用未授权"
                            )
                            # 不点击，保持 clicked=False，让上层逻辑感知失败
                        elif btn_count > 0:
                            # 找到第一个非错误按钮点击
                            target_btn = None
                            target_text = ""
                            for bi in range(btn_count):
                                bt = (all_buttons.nth(bi).text_content() or "").strip().lower()
                                if not any(err_kw in bt for err_kw in error_only_button_texts):
                                    target_btn = all_buttons.nth(bi)
                                    target_text = bt
                                    break
                            if target_btn is None:
                                # 全部都是错误按钮，已在上面处理；不应到这里
                                pass
                            else:
                                logger(f"Browser Form: consent_authorize fallback - clicking button: '{target_text}'")
                                try:
                                    with page.expect_navigation(timeout=15000, wait_until="commit"):
                                        target_btn.click(timeout=5000)
                                except Exception:
                                    try:
                                        target_btn.click(timeout=5000)
                                    except Exception:
                                        pass
                                    try:
                                        page.wait_for_url(
                                            lambda url: "callback" in url or "localhost" in url,
                                            timeout=10000,
                                        )
                                    except Exception:
                                        pass
                                clicked = True
                    except Exception as exc:
                        logger(f"Browser Form: consent_authorize fallback failed: {exc}")

                # 收尾：提取最终 URL 和 navigation_chain
                try:
                    current_url = page.url or current_url
                except Exception:
                    pass
                if current_url and (not navigation_chain or navigation_chain[-1] != current_url):
                    navigation_chain.append(current_url)
                try:
                    response_body = page.content()
                except Exception:
                    response_body = ""
                response_status = 200 if clicked else 404
                cookies = _extract_cookies(context)
                storage_state = _capture_storage_state(page)
                logger(
                    f"Browser Form: consent_authorize clicked={clicked}, "
                    f"final_url={current_url[:100]}"
                )
                return {
                    "status": response_status,
                    "body": response_body,
                    "cookies": cookies,
                    "cookies_count": len(cookies),
                    "challenge_passed": challenge_passed,
                    "sentinel_token": sentinel_token,
                    "final_url": current_url,
                    "page_type": "",
                    "continue_url": "",
                    "navigation_chain": navigation_chain,
                    "storage_state": storage_state,
                }

            if form_type_norm == "page_content":
                try:
                    response_status = int(initial_response.status) if initial_response else 200
                except Exception:
                    response_status = 200
                try:
                    response_body = page.content()
                except Exception as exc:
                    logger(f"Browser Form: page content extraction failed: {exc}")
                    response_body = ""

                logger(
                    f"Browser Form: response HTTP {response_status}"
                    + (f", len={len(response_body)}" if response_body else "")
                )
                if current_url and (not navigation_chain or navigation_chain[-1] != current_url):
                    navigation_chain.append(current_url)
                cookies = _extract_cookies(context)
                storage_state = _capture_storage_state(page)
                logger(
                    "Browser Form: state "
                    f"challenge_passed={'yes' if challenge_passed else 'no'}, "
                    f"page_type=-, final_url={current_url[:100]}"
                )
                return {
                    "status": response_status,
                    "body": response_body,
                    "cookies": cookies,
                    "cookies_count": len(cookies),
                    "challenge_passed": challenge_passed,
                    "sentinel_token": sentinel_token,
                    "final_url": current_url,
                    "page_type": "",
                    "continue_url": "",
                    "navigation_chain": navigation_chain,
                    "storage_state": storage_state,
                }

            api_request = _build_account_api_request(
                form_type_norm,
                form_value,
                current_url,
            )
            api_endpoint = api_request["endpoint"]
            api_body = api_request["body"]
            api_method = api_request["method"]
            sentinel_header_name = api_request["sentinel_header_name"]

            if not api_endpoint:
                logger(f"Browser Form: unsupported form_type: {form_type_norm}")
                return {
                    "status": -1,
                    "body": f"unsupported form type: {form_type_norm}",
                    "cookies": [],
                    "cookies_count": 0,
                }

            api_headers = _build_account_api_headers(
                current_url=current_url,
                sentinel_token=sentinel_token,
                sentinel_header_name=sentinel_header_name,
                device_id=device_id,
                session_fp=session_fp,
            )

            try:
                logger(f"Browser Form: API {api_method} {_safe_endpoint_path(api_endpoint)}")
                if api_method == "GET":
                    api_response = context.request.get(api_endpoint, headers=api_headers)
                else:
                    api_response = context.request.post(
                        api_endpoint,
                        headers=api_headers,
                        data=api_body,
                    )
                response_status = api_response.status
                response_body = api_response.text()
            except Exception as exc:
                logger(f"Browser Form: context.request failed: {exc}")
                return {
                    "status": -1,
                    "body": str(exc),
                    "cookies": [],
                    "cookies_count": 0,
                }

            logger(
                f"Browser Form: response HTTP {response_status}"
                + (f", len={len(response_body)}" if response_body else "")
            )

            current_url = str(page.url or page_url or "").strip()
            if current_url and (not navigation_chain or navigation_chain[-1] != current_url):
                navigation_chain.append(current_url)
            cookies = _extract_cookies(context)
            storage_state = _capture_storage_state(page)
            flow_meta = _extract_browser_flow_metadata(response_body, current_url)
            logger(
                "Browser Form: state "
                f"challenge_passed={'yes' if challenge_passed else 'no'}, "
                f"page_type={flow_meta['page_type'] or '-'}, "
                f"final_url={(flow_meta['final_url'] or current_url)[:100]}"
            )
            return {
                "status": response_status,
                "body": response_body,
                "cookies": cookies,
                "cookies_count": len(cookies),
                "challenge_passed": challenge_passed,
                "sentinel_token": sentinel_token,
                "final_url": flow_meta["final_url"] or current_url,
                "page_type": flow_meta["page_type"],
                "continue_url": flow_meta["continue_url"],
                "navigation_chain": navigation_chain,
                "storage_state": storage_state,
            }
        except Exception as exc:
            logger(f"Browser Form exception: {exc}")
            return None
        finally:
            browser.close()


def _run_browser_for_page(
    *,
    flow: str = "",
    proxy: Optional[str] = None,
    timeout_ms: int = 45000,
    page_url: Optional[str] = None,
    headless: bool = True,
    device_id: Optional[str] = None,
    log_fn: Optional[Callable[[str], None]] = None,
    extract_cookies: bool = False,
    skip_sentinel: bool = False,
    session_fp: Optional["SessionFingerprint"] = None,
) -> Optional[dict]:
    logger = log_fn or (lambda _msg: None)
    flow_name = str(flow or "").strip().lower()
    target_url = str(page_url or _flow_page_url(flow or "authorize_continue")).strip()

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        logger(f"Sentinel Browser unavailable: {exc}")
        return None

    launch_args = {
        "headless": bool(headless),
        "args": [
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
        ],
    }
    proxy_config = build_playwright_proxy_config(proxy)
    if proxy_config:
        launch_args["proxy"] = proxy_config

    logger(
        f"Browser start: {'warmup+cookies' if extract_cookies and skip_sentinel else 'sentinel'}"
        f", url={target_url}, headless={headless}"
        + (f", flow={flow_name}" if flow_name else "")
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**launch_args)
        try:
            context = _new_context(browser, device_id=device_id, session_fp=session_fp)
            page = context.new_page()
            navigation_chain = _record_navigation(page)

            logger("Browser: loading page and waiting for Cloudflare challenge...")
            page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)
            challenge_passed = False
            try:
                page.wait_for_function(
                    """() => {
                        const title = document.title || '';
                        const body = document.body ? document.body.innerText || '' : '';
                        return !title.includes('Just a moment') && !title.includes('Attention Required')
                            && !body.includes('Checking your browser') && !body.includes('cf-browser-verification');
                    }""",
                    timeout=min(timeout_ms, 20000),
                )
                challenge_passed = True
                logger("Browser: Cloudflare challenge passed")
            except Exception:
                logger("Browser: Cloudflare wait timed out, continuing")

            from .human_behavior_simulator import HumanBehaviorSimulator
            # 使用行为模拟器带来真实的思考或浏览延迟，取代 1500 毫秒死等
            HumanBehaviorSimulator().page_load_observation()

            cookies: list[dict] = []
            if extract_cookies:
                cookies = _extract_cookies(context)
                cf_present = any(cookie.get("name") == "cf_clearance" for cookie in cookies)
                logger(
                    f"Browser: extracted {len(cookies)} cookies"
                    + (f", cf_clearance={'yes' if cf_present else 'no'}" if cookies else "")
                )

            sentinel_token: Optional[str] = None
            if not skip_sentinel:
                page.wait_for_function(
                    "() => typeof window.SentinelSDK !== 'undefined' && typeof window.SentinelSDK.token === 'function'",
                    timeout=min(timeout_ms, 15000),
                )
                result = page.evaluate(
                    """
                    async ({ flow }) => {
                        try {
                            const token = await window.SentinelSDK.token(flow);
                            return { success: true, token };
                        } catch (e) {
                            return {
                                success: false,
                                error: (e && (e.message || String(e))) || "unknown",
                            };
                        }
                    }
                    """,
                    {"flow": flow_name},
                )
                if result and result.get("success") and result.get("token"):
                    token = str(result["token"] or "").strip()
                    if token:
                        sentinel_token = token
                        try:
                            parsed = json.loads(token)
                            logger(
                                "Sentinel Browser success: "
                                f"p={'yes' if parsed.get('p') else 'no'} "
                                f"t={'yes' if parsed.get('t') else 'no'} "
                                f"c={'yes' if parsed.get('c') else 'no'}"
                            )
                        except Exception:
                            logger(f"Sentinel Browser success: len={len(token)}")
                else:
                    logger(
                        "Sentinel Browser failed: "
                        + str((result or {}).get("error") or "no result")
                    )

            final_url = str(page.url or target_url or "").strip()
            if final_url and (not navigation_chain or navigation_chain[-1] != final_url):
                navigation_chain.append(final_url)
            storage_state = _capture_storage_state(page)
            return {
                "sentinel_token": sentinel_token,
                "cookies": cookies,
                "cookies_count": len(cookies),
                "challenge_passed": challenge_passed,
                "final_url": final_url,
                "navigation_chain": navigation_chain,
                "storage_state": storage_state,
            }
        except Exception as exc:
            logger(f"Browser exception: {exc}")
            return None
        finally:
            browser.close()
