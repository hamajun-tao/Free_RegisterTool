"""
ChatGPT registration client module.
Uses curl_cffi to simulate browser requests.
"""

import os
import random
import uuid
import time
from urllib.parse import urlparse
from core.proxy_utils import build_requests_proxy_config

try:
    from curl_cffi import requests as curl_requests
except ImportError:
    print("[ERROR] curl_cffi required: pip install curl_cffi")
    import sys

    sys.exit(1)

from .sentinel_token import build_sentinel_token
from .sentinel_browser import get_sentinel_token_via_browser
from .utils import (
    FlowState,
    build_browser_headers,
    decode_jwt_payload,
    describe_flow_state,
    extract_flow_state,
    generate_datadog_trace,
    normalize_flow_url,
    random_delay,
    seed_oai_device_cookie,
)


# Chrome fingerprint profiles
_CHROME_PROFILES = [
    {
        "major": 131,
        "impersonate": "chrome131",
        "build": 6778,
        "patch_range": (69, 205),
        "sec_ch_ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    },
    {
        "major": 133,
        "impersonate": "chrome133a",
        "build": 6943,
        "patch_range": (33, 153),
        "sec_ch_ua": '"Not(A:Brand";v="99", "Google Chrome";v="133", "Chromium";v="133"',
    },
    {
        "major": 136,
        "impersonate": "chrome136",
        "build": 7103,
        "patch_range": (48, 175),
        "sec_ch_ua": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
    },
]


def _random_chrome_version():
    """Pick a random Chrome version profile."""
    profile = random.choice(_CHROME_PROFILES)
    major = profile["major"]
    build = profile["build"]
    patch = random.randint(*profile["patch_range"])
    full_ver = f"{major}.0.{build}.{patch}"
    ua = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{full_ver} Safari/537.36"
    return profile["impersonate"], major, full_ver, ua, profile["sec_ch_ua"]


class ChatGPTClient:
    """ChatGPT registration client."""

    BASE = "https://chatgpt.com"
    AUTH = "https://auth.openai.com"

    def __init__(self, proxy=None, verbose=False, browser_mode="protocol", skip_browser_sentinel=False, skip_sentinel_entirely=False):
        """
        Initialize ChatGPT client.

        Args:
            proxy: proxy address
            verbose: enable verbose logging
            browser_mode: protocol | headless | headed
            skip_browser_sentinel: skip browser Sentinel (saves ~5s timeout when proxy is known unavailable)
            skip_sentinel_entirely: skip Sentinel token entirely (for registration_disallowed retry strategy)
        """
        self.proxy = proxy
        self.verbose = verbose
        self.browser_mode = browser_mode or "protocol"
        self.skip_browser_sentinel = skip_browser_sentinel
        self.skip_sentinel_entirely = skip_sentinel_entirely
        self.device_id = str(uuid.uuid4())
        self.accept_language = random.choice(
            [
                "en-US,en;q=0.9",
                "en-US,en;q=0.9,zh-CN;q=0.8",
                "en,en-US;q=0.9",
                "en-US,en;q=0.8",
            ]
        )

        # Random Chrome version
        (
            self.impersonate,
            self.chrome_major,
            self.chrome_full,
            self.ua,
            self.sec_ch_ua,
        ) = _random_chrome_version()

        # Create session
        self.session = curl_requests.Session(impersonate=self.impersonate)

        if self.proxy:
            self.session.proxies = build_requests_proxy_config(self.proxy)

        # Set base headers
        self.session.headers.update(
            {
                "User-Agent": self.ua,
                "Accept-Language": self.accept_language,
                "sec-ch-ua": self.sec_ch_ua,
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-ch-ua-arch": '"x86"',
                "sec-ch-ua-bitness": '"64"',
                "sec-ch-ua-full-version": f'"{self.chrome_full}"',
                "sec-ch-ua-platform-version": f'"{random.randint(10, 15)}.0.0"',
            }
        )

        # Set oai-did cookie
        seed_oai_device_cookie(self.session, self.device_id)
        self.last_registration_state = FlowState()
        self._email_otp_context = {}
        self._otp_sent_at = None

    def _get_sentinel_token(self, flow: str, *, page_url: str | None = None):
        if self.skip_sentinel_entirely:
            self._log(f"{flow}: skip sentinel token generation (no-sentinel mode)")
            return None
        prefer_browser = flow in {"username_password_create", "oauth_create_account"}
        if prefer_browser and not self.skip_browser_sentinel:
            try:
                has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
                force_headless = not has_display
                token = get_sentinel_token_via_browser(
                    flow=flow,
                    proxy=self.proxy,
                    page_url=page_url,
                    headless=force_headless,
                    device_id=self.device_id,
                    log_fn=lambda msg: self._log(msg),
                )
                if token:
                    return token
                self._log(f"{flow}: browser Sentinel returned no token, fallback to HTTP PoW")
            except Exception as exc:
                self._log(f"{flow}: browser Sentinel unavailable ({type(exc).__name__}), fallback to HTTP PoW")
        elif prefer_browser and self.skip_browser_sentinel:
            self._log(f"{flow}: skip browser Sentinel (proxy known unavailable), direct HTTP PoW")

        # HTTP PoW sentinel with retry
        for retry in range(3):
            token = build_sentinel_token(
                self.session,
                self.device_id,
                flow=flow,
                user_agent=self.ua,
                sec_ch_ua=self.sec_ch_ua,
                impersonate=self.impersonate,
            )
            if token:
                if retry:
                    self._log(f"{flow}: HTTP PoW token obtained (retry {retry})")
                return token
            if retry < 2:
                import time
                time.sleep(0.5)
        self._log(f"{flow}: HTTP PoW Sentinel failed", "warning")
        return None

    def _log(self, msg, level="info"):
        """Output log message. Only prints when verbose is enabled or level is warning/error."""
        if self.verbose or level in ("warning", "error"):
            prefix = {"warning": "[WARN]", "error": "[ERROR]"}.get(level, "")
            if prefix:
                print(f"  {prefix} {msg}")
            elif self.verbose:
                print(f"  {msg}")

    def _capture_email_otp_context(self, data):
        """Extract OTP-related context fields from send-otp response payload."""
        context = {}
        if not isinstance(data, dict):
            self._email_otp_context = context
            return context

        page = data.get("page") if isinstance(data.get("page"), dict) else {}
        payload = page.get("payload") if isinstance(page.get("payload"), dict) else {}

        candidate_keys = (
            "email_verification_mode",
            "pending_authentication_token",
            "pendingAuthenticationToken",
            "email_verification_id",
            "emailVerificationId",
            "state",
            "challenge",
            "challenge_id",
            "challengeId",
            "flow_id",
            "flowId",
            "login_challenge_id",
            "loginChallengeId",
        )

        for key in candidate_keys:
            value = payload.get(key)
            if value in (None, ""):
                value = data.get(key)
            if value not in (None, ""):
                context[key] = value

        self._email_otp_context = context
        return context

    def _browser_pause(self, low=0.3, high=0.9):
        """Add a small human-like delay between browser-like actions."""
        behavior_sim = getattr(self, "_behavior_sim", None)
        if behavior_sim:
            behavior_sim.natural_delay(low, high)
        else:
            random_delay(low, high)

    def _headers(
        self,
        url,
        *,
        accept,
        referer=None,
        origin=None,
        content_type=None,
        navigation=False,
        fetch_mode=None,
        fetch_dest=None,
        fetch_site=None,
        extra_headers=None,
    ):
        return build_browser_headers(
            url=url,
            user_agent=self.ua,
            sec_ch_ua=self.sec_ch_ua,
            chrome_full_version=self.chrome_full,
            accept=accept,
            accept_language=self.accept_language,
            referer=referer,
            origin=origin,
            content_type=content_type,
            navigation=navigation,
            fetch_mode=fetch_mode,
            fetch_dest=fetch_dest,
            fetch_site=fetch_site,
            headed=self.browser_mode == "headed",
            extra_headers=extra_headers,
        )

    def _reset_session(self):
        """Reset browser fingerprint/session to recover from intermediate blocks."""
        self.device_id = str(uuid.uuid4())
        (
            self.impersonate,
            self.chrome_major,
            self.chrome_full,
            self.ua,
            self.sec_ch_ua,
        ) = _random_chrome_version()
        self.accept_language = random.choice(
            [
                "en-US,en;q=0.9",
                "en-US,en;q=0.9,zh-CN;q=0.8",
                "en,en-US;q=0.9",
                "en-US,en;q=0.8",
            ]
        )

        self.session = curl_requests.Session(impersonate=self.impersonate)
        if self.proxy:
            self.session.proxies = build_requests_proxy_config(self.proxy)

        self.session.headers.update(
            {
                "User-Agent": self.ua,
                "Accept-Language": self.accept_language,
                "sec-ch-ua": self.sec_ch_ua,
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-ch-ua-arch": '"x86"',
                "sec-ch-ua-bitness": '"64"',
                "sec-ch-ua-full-version": f'"{self.chrome_full}"',
                "sec-ch-ua-platform-version": f'"{random.randint(10, 15)}.0.0"',
            }
        )
        seed_oai_device_cookie(self.session, self.device_id)
        self._otp_sent_at = None

    def _is_transient_network_error(self, exc):
        """Whether an exception looks like a transient transport-level failure."""
        text = str(exc or "").lower()
        markers = (
            "curl: (56)",
            "connection closed abruptly",
            "recv failure",
            "connection reset",
            "empty reply from server",
            "http2 stream",
            "proxy connect aborted",
            "failed sending data to the peer",
            "send failure",
            "tls connect error",
            "timed out",
            "timeout",
        )
        return any(marker in text for marker in markers)

    def _recreate_transport_session(self):
        """Recreate curl session transport while preserving cookies and fingerprint."""
        old_session = self.session
        old_headers = {}
        cookie_jar = []

        try:
            old_headers = dict(getattr(old_session, "headers", {}) or {})
        except Exception:
            old_headers = {}

        try:
            for c in getattr(getattr(old_session, "cookies", None), "jar", []) or []:
                name = str(getattr(c, "name", "") or "").strip()
                if not name:
                    continue
                cookie_jar.append(
                    {
                        "name": name,
                        "value": str(getattr(c, "value", "") or ""),
                        "domain": str(getattr(c, "domain", "") or ""),
                        "path": str(getattr(c, "path", "") or "/"),
                        "secure": bool(getattr(c, "secure", False)),
                    }
                )
        except Exception:
            cookie_jar = []

        self.session = curl_requests.Session(impersonate=self.impersonate)
        if self.proxy:
            self.session.proxies = build_requests_proxy_config(self.proxy)

        self.session.headers.update(
            {
                "User-Agent": self.ua,
                "Accept-Language": self.accept_language,
                "sec-ch-ua": self.sec_ch_ua,
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-ch-ua-arch": '"x86"',
                "sec-ch-ua-bitness": '"64"',
                "sec-ch-ua-full-version": f'"{self.chrome_full}"',
                "sec-ch-ua-platform-version": f'"{random.randint(10, 15)}.0.0"',
            }
        )
        if old_headers:
            self.session.headers.update(old_headers)

        restored = 0
        for item in cookie_jar:
            try:
                self.session.cookies.set(
                    item["name"],
                    item["value"],
                    domain=item["domain"],
                    path=item["path"] or "/",
                    secure=item["secure"],
                )
                restored += 1
            except Exception:
                continue

        if restored == 0:
            try:
                for key, value in (getattr(old_session.cookies, "get_dict", lambda: {})() or {}).items():
                    self.session.cookies.set(str(key), str(value))
                    restored += 1
            except Exception:
                pass

        seed_oai_device_cookie(self.session, self.device_id)
        self._log(f"create_account: transport session recreated, restored_cookies={restored}")

    def _state_from_url(self, url, method="GET"):
        state = extract_flow_state(
            current_url=normalize_flow_url(url, auth_base=self.AUTH),
            auth_base=self.AUTH,
            default_method=method,
        )
        if method:
            state.method = str(method).upper()
        return state

    def _state_from_payload(self, data, current_url=""):
        return extract_flow_state(
            data=data,
            current_url=current_url,
            auth_base=self.AUTH,
        )

    def _state_signature(self, state: FlowState):
        return (
            state.page_type or "",
            state.method or "",
            state.continue_url or "",
            state.current_url or "",
        )

    def _is_registration_complete_state(self, state: FlowState):
        current_url = (state.current_url or "").lower()
        continue_url = (state.continue_url or "").lower()
        page_type = state.page_type or ""
        return (
            page_type in {"callback", "chatgpt_home", "oauth_callback"}
            or ("chatgpt.com" in current_url and "redirect_uri" not in current_url)
            or (
                "chatgpt.com" in continue_url
                and "redirect_uri" not in continue_url
                and page_type != "external_url"
            )
        )

    def _state_is_password_registration(self, state: FlowState):
        return state.page_type in {"create_account_password", "password"}

    def _state_is_email_otp(self, state: FlowState):
        target = f"{state.continue_url} {state.current_url}".lower()
        return (
            state.page_type == "email_otp_verification"
            or "email-verification" in target
            or "email-otp" in target
        )

    def _state_is_about_you(self, state: FlowState):
        target = f"{state.continue_url} {state.current_url}".lower()
        return state.page_type == "about_you" or "about-you" in target

    def _state_requires_navigation(self, state: FlowState):
        if (state.method or "GET").upper() != "GET":
            return False
        if state.page_type == "external_url" and state.continue_url:
            return True
        if state.continue_url and state.continue_url != state.current_url:
            return True
        return False

    def _follow_flow_state(self, state: FlowState, referer=None):
        """Follow continue_url and advance registration state machine."""
        target_url = state.continue_url or state.current_url
        if not target_url:
            return False, "missing continue_url"

        try:
            self._browser_pause()
            r = self.session.get(
                target_url,
                headers=self._headers(
                    target_url,
                    accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    referer=referer,
                    navigation=True,
                ),
                allow_redirects=True,
                timeout=30,
            )
            final_url = str(r.url)

            content_type = (r.headers.get("content-type", "") or "").lower()
            if "application/json" in content_type:
                try:
                    next_state = self._state_from_payload(
                        r.json(), current_url=final_url
                    )
                except Exception:
                    next_state = self._state_from_url(final_url)
            else:
                next_state = self._state_from_url(final_url)

            return True, next_state
        except Exception as e:
            self._log(f"follow continue_url failed: {e}", "warning")
            return False, str(e)

    def _get_cookie_value(self, name, domain_hint=None):
        """Read current cookie value by name/domain hint."""
        for cookie in self.session.cookies.jar:
            if cookie.name != name:
                continue
            if domain_hint and domain_hint not in (cookie.domain or ""):
                continue
            return cookie.value
        return ""

    def get_next_auth_session_token(self):
        """Get `__Secure-next-auth.session-token` cookie value."""
        return self._get_cookie_value("__Secure-next-auth.session-token", "chatgpt.com")

    def fetch_chatgpt_session(self):
        """Request ChatGPT session endpoint and return raw session payload."""
        url = f"{self.BASE}/api/auth/session"
        self._browser_pause()
        response = self.session.get(
            url,
            headers=self._headers(
                url,
                accept="application/json",
                referer=f"{self.BASE}/",
                fetch_site="same-origin",
            ),
            timeout=30,
        )
        if response.status_code != 200:
            return False, f"/api/auth/session -> HTTP {response.status_code}"

        try:
            data = response.json()
        except Exception as exc:
            return False, f"/api/auth/session returned non-JSON: {exc}"

        access_token = str(data.get("accessToken") or "").strip()
        if not access_token:
            return False, "/api/auth/session returned no accessToken"
        return True, data

    def reuse_session_and_get_tokens(self):
        """
        Reuse the registration session to get ChatGPT Session / AccessToken.

        Returns:
            tuple[bool, dict|str]: (success, normalized token/session data or error message)
        """
        state = self.last_registration_state or FlowState()
        self._log("Step 1/4: follow registration callback...")
        if state.page_type == "external_url" or self._state_requires_navigation(state):
            ok, followed = self._follow_flow_state(
                state,
                referer=state.current_url or f"{self.AUTH}/about-you",
            )
            if not ok:
                return False, f"registration callback failed: {followed}"
            self.last_registration_state = followed

        self._log("Step 2/4: check __Secure-next-auth.session-token ...")
        session_cookie = self.get_next_auth_session_token()
        if not session_cookie:
            return False, "missing __Secure-next-auth.session-token"

        self._log("Step 3/4: request ChatGPT /api/auth/session ...")
        ok, session_or_error = self.fetch_chatgpt_session()
        if not ok:
            return False, session_or_error

        session_data = session_or_error
        access_token = str(session_data.get("accessToken") or "").strip()
        session_token = str(
            session_data.get("sessionToken") or session_cookie or ""
        ).strip()
        user = session_data.get("user") or {}
        account = session_data.get("account") or {}
        jwt_payload = decode_jwt_payload(access_token)
        auth_payload = jwt_payload.get("https://api.openai.com/auth") or {}

        account_id = (
            str(account.get("id") or "").strip()
            or str(auth_payload.get("chatgpt_account_id") or "").strip()
        )
        user_id = (
            str(user.get("id") or "").strip()
            or str(auth_payload.get("chatgpt_user_id") or "").strip()
            or str(auth_payload.get("user_id") or "").strip()
        )

        normalized = {
            "access_token": access_token,
            "session_token": session_token,
            "account_id": account_id,
            "user_id": user_id,
            "workspace_id": account_id,
            "expires": session_data.get("expires"),
            "user": user,
            "account": account,
            "auth_provider": session_data.get("authProvider"),
            "raw_session": session_data,
        }

        self._log("Step 4/4: accessToken extracted")
        if account_id:
            self._log(f"Session Account ID: {account_id}")
        return True, normalized

    def visit_homepage(self):
        """Visit homepage to establish session."""
        url = f"{self.BASE}/"
        try:
            self._browser_pause()
            r = self.session.get(
                url,
                headers=self._headers(
                    url,
                    accept="text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    navigation=True,
                ),
                allow_redirects=True,
                timeout=30,
            )
            if r.status_code != 200:
                return False
            if not self._has_cf_clearance_cookie():
                self._log("Homepage session not ready: cf_clearance=no", "warning")
            return True
        except Exception as e:
            self._log(f"Homepage visit failed: {e}", "warning")
            return False

    def _has_cf_clearance_cookie(self):
        try:
            cookies_dict = self.session.cookies.get_dict() or {}
            if cookies_dict.get("cf_clearance"):
                return True
            for c in getattr(self.session.cookies, "jar", []):
                if getattr(c, "name", "") == "cf_clearance" and getattr(c, "value", ""):
                    return True
        except Exception:
            return False
        return False

    def get_csrf_token(self):
        """Get CSRF token."""
        url = f"{self.BASE}/api/auth/csrf"
        try:
            r = self.session.get(
                url,
                headers=self._headers(
                    url,
                    accept="application/json",
                    referer=f"{self.BASE}/",
                    fetch_site="same-origin",
                ),
                timeout=30,
            )

            if r.status_code == 200:
                data = r.json()
                token = data.get("csrfToken", "")
                if token:
                    return token
        except Exception as e:
            self._log(f"CSRF token failed: {e}", "warning")

        return None

    def signin(self, email, csrf_token):
        """
        Submit email to get authorize URL.

        Returns:
            str: authorize URL
        """
        url = f"{self.BASE}/api/auth/signin/openai"

        params = {
            "prompt": "login",
            "ext-oai-did": self.device_id,
            "auth_session_logging_id": str(uuid.uuid4()),
            "screen_hint": "login_or_signup",
            "login_hint": email,
        }

        form_data = {
            "callbackUrl": f"{self.BASE}/",
            "csrfToken": csrf_token,
            "json": "true",
        }

        try:
            self._browser_pause()
            r = self.session.post(
                url,
                params=params,
                data=form_data,
                headers=self._headers(
                    url,
                    accept="application/json",
                    referer=f"{self.BASE}/",
                    origin=self.BASE,
                    content_type="application/x-www-form-urlencoded",
                    fetch_site="same-origin",
                ),
                timeout=30,
            )

            if r.status_code == 200:
                data = r.json()
                authorize_url = data.get("url", "")
                if authorize_url:
                    return authorize_url
        except Exception as e:
            self._log(f"Signin failed: {e}", "warning")

        return None

    def authorize(self, url, max_retries=3):
        """
        Visit authorize URL, follow redirects (with retry).

        Returns:
            str: final redirect URL
        """
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    self._log(f"Authorize retry {attempt + 1}/{max_retries}")
                    time.sleep(1)

                self._browser_pause()
                r = self.session.get(
                    url,
                    headers=self._headers(
                        url,
                        accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        referer=f"{self.BASE}/",
                        navigation=True,
                    ),
                    allow_redirects=True,
                    timeout=30,
                )

                final_url = str(r.url)
                return final_url

            except Exception as e:
                error_msg = str(e)
                is_tls_error = (
                    "TLS" in error_msg
                    or "SSL" in error_msg
                    or "curl: (35)" in error_msg
                )

                if is_tls_error and attempt < max_retries - 1:
                    self._log(f"Authorize TLS error (attempt {attempt + 1}/{max_retries}): {error_msg[:100]}", "warning")
                    continue
                else:
                    self._log(f"Authorize failed: {e}", "warning")
                    return ""

        return ""

    def callback(self, callback_url=None, referer=None):
        """Complete registration callback."""
        url = callback_url or f"{self.AUTH}/api/accounts/authorize/callback"
        ok, _ = self._follow_flow_state(
            self._state_from_url(url),
            referer=referer or f"{self.AUTH}/about-you",
        )
        return ok

    def register_user(self, email, password):
        """
        Register user with email + password.

        Returns:
            tuple: (success, message)
        """
        url = f"{self.AUTH}/api/accounts/user/register"

        headers = self._headers(
            url,
            accept="application/json",
            referer=f"{self.AUTH}/create-account/password",
            origin=self.AUTH,
            content_type="application/json",
            fetch_site="same-origin",
        )
        headers.update(generate_datadog_trace())
        headers["oai-device-id"] = self.device_id

        sentinel_token = self._get_sentinel_token(
            "username_password_create",
            page_url=f"{self.AUTH}/create-account/password",
        )
        if sentinel_token:
            headers["openai-sentinel-token"] = sentinel_token

        payload = {
            "username": email,
            "password": password,
        }

        try:
            self._browser_pause()
            r = self.session.post(url, json=payload, headers=headers, timeout=30)

            if r.status_code == 200:
                return True, "registration success"
            else:
                try:
                    error_data = r.json()
                    error_msg = error_data.get("error", {}).get("message", r.text[:200])
                except:
                    error_msg = r.text[:200]
                self._log(f"Register failed: {r.status_code} - {error_msg}", "warning")
                return False, f"HTTP {r.status_code}: {error_msg}"

        except Exception as e:
            self._log(f"Register exception: {e}", "warning")
            return False, str(e)

    def send_email_otp(self, referer=None, return_state=False):
        """Trigger sending email OTP."""
        url = f"{self.AUTH}/api/accounts/email-otp/send"
        referer_url = referer or f"{self.AUTH}/email-verification"

        try:
            self._browser_pause()
            r = self.session.post(
                url,
                headers=self._headers(
                    url,
                    accept="application/json",
                    referer=referer_url,
                    content_type="application/json",
                    origin=self.AUTH,
                    fetch_site="same-origin",
                ),
                allow_redirects=True,
                timeout=30,
            )
            response_text = getattr(r, "text", "")
            if not isinstance(response_text, str):
                response_text = ""
            body_preview = response_text[:240].replace("\n", " ").replace("\r", " ")
            self._log(f"send_otp response: HTTP {r.status_code}")
            if body_preview:
                self._log(f"send_otp body: {body_preview}")

            ok = r.status_code == 200
            if ok and response_text:
                lowered = response_text.lower()
                silent_fail_keywords = [
                    "rate_limit", "too_many", "try again later",
                    "invalid_session", "unauthorized", "forbidden",
                    "captcha_required", "challenge_required",
                    "email_not_allowed", "email_blocked",
                ]
                for kw in silent_fail_keywords:
                    if kw in lowered:
                        self._log(f"send_otp blocked: body contains '{kw}'", "warning")
                        ok = False
                        break
                if ok and response_text.strip() in ("{}", ""):
                    self._log("send_otp returned empty body; delivery may have silently failed", "warning")

            next_state = None
            if ok:
                self._otp_sent_at = time.time()
                try:
                    data = r.json()
                except Exception:
                    data = {}
                if not isinstance(data, dict):
                    data = {}
                otp_context = self._capture_email_otp_context(data)
                if otp_context:
                    keys_text = ", ".join(sorted(otp_context.keys()))
                    self._log(f"OTP context keys: {keys_text}")
                response_url = getattr(r, "url", "")
                if not isinstance(response_url, str):
                    response_url = ""
                next_state = self._state_from_payload(
                    data,
                    current_url=response_url or referer_url or f"{self.AUTH}/email-verification",
                )
            if return_state:
                return ok, next_state
            return ok
        except Exception as e:
            self._log(f"send_otp exception: {type(e).__name__}: {e}", "warning")
            return (False, None) if return_state else False

    def verify_email_otp(self, otp_code, return_state=False, referer=None):
        """
        Verify email OTP code.

        Args:
            otp_code: 6-digit verification code

        Returns:
            tuple: (success, message)
        """
        url = f"{self.AUTH}/api/accounts/email-otp/validate"

        headers = self._headers(
            url,
            accept="application/json",
            referer=referer or f"{self.AUTH}/email-verification",
            origin=self.AUTH,
            content_type="application/json",
            fetch_site="same-origin",
        )
        headers.update(generate_datadog_trace())
        headers["oai-device-id"] = self.device_id

        payload = {"code": otp_code}
        context = dict(getattr(self, "_email_otp_context", {}) or {})
        for key in (
            "email_verification_mode",
            "pending_authentication_token",
            "pendingAuthenticationToken",
            "email_verification_id",
            "emailVerificationId",
            "state",
            "challenge",
            "challenge_id",
            "challengeId",
            "flow_id",
            "flowId",
            "login_challenge_id",
            "loginChallengeId",
        ):
            value = context.get(key)
            if value not in (None, ""):
                payload[key] = value

        try:
            self._browser_pause()
            r = self.session.post(
                url,
                json=payload,
                headers=headers,
                timeout=30,
                allow_redirects=False,
            )

            if r.status_code == 400:
                try:
                    err_data = r.json()
                except Exception:
                    err_data = {}
                err_obj = err_data.get("error") if isinstance(err_data, dict) else {}
                offending = str((err_obj or {}).get("param") or "").strip()
                err_code = str((err_obj or {}).get("code") or "").strip()
                if offending and err_code in {"unknown_parameter", "invalid_request_error"} and offending in payload:
                    self._log(f"OTP payload field '{offending}' was rejected; remove it and retry once", "warning")
                    payload.pop(offending, None)
                    r = self.session.post(
                        url,
                        json=payload,
                        headers=headers,
                        timeout=30,
                        allow_redirects=False,
                    )

            if r.status_code == 200:
                try:
                    data = r.json()
                except Exception:
                    data = {}
                next_state = self._state_from_payload(
                    data, current_url=str(r.url) or f"{self.AUTH}/about-you"
                )
                self._log(f"OTP verified: {describe_flow_state(next_state)}")
                return (True, next_state) if return_state else (True, "verification success")
            else:
                error_msg = r.text[:200]
                self._log(f"OTP verify failed: {r.status_code} - {error_msg}", "warning")
                return False, f"HTTP {r.status_code}: {error_msg}"

        except Exception as e:
            self._log(f"OTP verify exception: {e}", "warning")
            return False, str(e)

    def create_account(self, first_name, last_name, birthdate, return_state=False):
        """
        Complete account creation (submit name and birthdate).

        Args:
            first_name: first name
            last_name: last name
            birthdate: birthdate (YYYY-MM-DD)

        Returns:
            tuple: (success, message)
        """
        name = f"{first_name} {last_name}"
        self._log(f"Creating account: {name}")

        # Pre-visit /about-you page to simulate real browser behavior
        about_you_url = f"{self.AUTH}/about-you"
        try:
            self._browser_pause(0.5, 1.2)
            pre_visit_headers = self._headers(
                about_you_url,
                accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                referer=f"{self.AUTH}/email-verification",
                fetch_site="same-origin",
            )
            pre_visit_headers["sec-fetch-dest"] = "document"
            pre_r = self.session.get(about_you_url, headers=pre_visit_headers, timeout=30)
        except Exception as pre_exc:
            self._log(f"about-you pre-visit exception (non-blocking): {type(pre_exc).__name__}: {pre_exc}")

        url = f"{self.AUTH}/api/accounts/create_account"

        sentinel_token = self._get_sentinel_token(
            "oauth_create_account",
            page_url=f"{self.AUTH}/about-you",
        )
        if not sentinel_token:
            self._log("create_account: no sentinel token, continue with degraded request")

        headers = self._headers(
            url,
            accept="application/json",
            referer=f"{self.AUTH}/about-you",
            origin=self.AUTH,
            content_type="application/json",
            fetch_site="same-origin",
            extra_headers={
                "oai-device-id": self.device_id,
            },
        )
        if sentinel_token:
            headers["openai-sentinel-token"] = sentinel_token
        headers.update(generate_datadog_trace())

        payload = {
            "name": name,
            "birthdate": birthdate,
        }

        max_attempts = 3
        last_error = None
        for attempt in range(max_attempts):
            try:
                self._browser_pause()
                r = self.session.post(url, json=payload, headers=headers, timeout=30)

                if r.status_code == 200:
                    try:
                        data = r.json()
                    except Exception:
                        data = {}
                    next_state = self._state_from_payload(
                        data, current_url=str(r.url) or self.BASE
                    )
                    self._log(f"Account created: {describe_flow_state(next_state)}")
                    return (True, next_state) if return_state else (True, "account created")

                error_msg = r.text[:200]
                self._log(f"Create account failed: {r.status_code} - {error_msg}", "warning")
                return False, f"HTTP {r.status_code}: {error_msg}"

            except Exception as e:
                last_error = e
                if not self._is_transient_network_error(e) or attempt >= max_attempts - 1:
                    break

                if attempt == 0:
                    self._log(f"create_account transient network error, retry in-session: {e}")
                    time.sleep(0.8)
                    continue

                self._log(f"create_account transient network error, recreate transport and retry: {e}")
                self._recreate_transport_session()
                time.sleep(1.1)

        self._log(f"Create account exception: {last_error}", "warning")
        return False, str(last_error)

    def register_complete_flow(
        self, email, password, first_name, last_name, birthdate, skymail_client
    ):
        """
        Complete registration flow.

        Args:
            email: email address
            password: password
            first_name: first name
            last_name: last name
            birthdate: birthdate
            skymail_client: Skymail client (for getting verification codes)

        Returns:
            tuple: (success, message)
        """
        from urllib.parse import urlparse

        max_auth_attempts = 5
        final_url = ""
        final_path = ""

        for auth_attempt in range(max_auth_attempts):
            if auth_attempt > 0:
                self._log(f"Pre-auth retry {auth_attempt + 1}/{max_auth_attempts}...")
                self._reset_session()
                time.sleep(min(2.0 * auth_attempt, 6.0))

            # 1. Visit homepage
            if not self.visit_homepage():
                if auth_attempt < max_auth_attempts - 1:
                    continue
                return False, "homepage visit failed"

            # 2. Get CSRF token
            csrf_token = self.get_csrf_token()
            if not csrf_token:
                if auth_attempt < max_auth_attempts - 1:
                    continue
                return False, "CSRF token failed"

            # 3. Submit email, get authorize URL
            auth_url = self.signin(email, csrf_token)
            if not auth_url:
                if auth_attempt < max_auth_attempts - 1:
                    continue
                return False, "signin failed"

            # 4. Visit authorize URL
            final_url = self.authorize(auth_url)
            if not final_url:
                if auth_attempt < max_auth_attempts - 1:
                    continue
                return False, "authorize failed"

            final_path = urlparse(final_url).path
            self._log(f"Authorize -> {final_path}")

            if "api/accounts/authorize" in final_path or final_path == "/error":
                self._log(
                    f"Cloudflare/SPA intermediate page detected: {final_url[:160]}..."
                )
                if auth_attempt < max_auth_attempts - 1:
                    continue
                return False, f"pre-auth blocked: {final_path}"

            break

        state = self._state_from_url(final_url)
        self._log(f"Registration state: {describe_flow_state(state)}")

        register_submitted = False
        otp_verified = False
        account_created = False
        otp_send_attempted = False
        seen_states = {}

        def _normalize_send_otp_result(result):
            if isinstance(result, tuple):
                if len(result) >= 2:
                    return bool(result[0]), result[1]
                if len(result) == 1:
                    return bool(result[0]), None
            return bool(result), None

        for _ in range(12):
            signature = self._state_signature(state)
            seen_states[signature] = seen_states.get(signature, 0) + 1
            if seen_states[signature] > 2:
                return False, f"State stuck: {describe_flow_state(state)}"

            if self._is_registration_complete_state(state):
                self.last_registration_state = state
                self._log("Registration flow complete")
                return True, "registration success"

            if self._state_is_password_registration(state):
                if register_submitted:
                    return False, "password stage re-entered"
                success, msg = self.register_user(email, password)
                if not success:
                    return False, f"register failed: {msg}"
                register_submitted = True
                otp_send_attempted = True
                send_ok, send_state = _normalize_send_otp_result(
                    self.send_email_otp(
                        referer=state.current_url
                        or state.continue_url
                        or f"{self.AUTH}/create-account/password",
                        return_state=True,
                    )
                )
                if not send_ok:
                    self._log("send_otp returned failure, continuing to wait for email OTP...")
                    state = self._state_from_url(f"{self.AUTH}/email-verification")
                else:
                    state = send_state or self._state_from_url(f"{self.AUTH}/email-verification")

            if self._state_is_email_otp(state):
                if not otp_send_attempted:
                    otp_send_attempted = True
                    self._log("email-verification session exists, waiting for existing OTP...")

                max_resends = 3
                otp_code = None
                for resend_attempt in range(max_resends):
                    if resend_attempt == 0 and not register_submitted:
                        wait_time = 30
                    else:
                        wait_time = 45 if resend_attempt < max_resends - 1 else 120
                    self._log(f"Waiting for OTP... (round {resend_attempt+1}, {wait_time}s)")
                    try:
                        otp_code = skymail_client.wait_for_verification_code(
                            email,
                            timeout=wait_time,
                            otp_sent_at=self._otp_sent_at,
                        )
                    except TimeoutError:
                        otp_code = None
                        self._log(
                            f"Round {resend_attempt+1} OTP wait timeout",
                            "warning",
                        )
                    if otp_code:
                        break
                    if resend_attempt < max_resends - 1:
                        self._log(f"No OTP received, triggering resend (attempt {resend_attempt+1})...")
                        send_ok, send_state = _normalize_send_otp_result(
                            self.send_email_otp(
                                referer=state.current_url
                                or state.continue_url
                                or f"{self.AUTH}/email-verification",
                                return_state=True,
                            )
                        )
                        if send_state:
                            state = send_state
                if not otp_code:
                    return False, "OTP not received"

                success, next_state = self.verify_email_otp(
                    otp_code,
                    return_state=True,
                    referer=state.current_url
                    or state.continue_url
                    or f"{self.AUTH}/email-verification",
                )
                if not success:
                    return False, f"OTP verification failed: {next_state}"
                otp_verified = True
                state = next_state
                self.last_registration_state = state
                continue

            if self._state_is_about_you(state):
                if account_created:
                    return False, "about-you stage re-entered"
                success, next_state = self.create_account(
                    first_name,
                    last_name,
                    birthdate,
                    return_state=True,
                )
                if not success:
                    return False, f"create account failed: {next_state}"
                account_created = True
                state = next_state
                self.last_registration_state = state
                continue

            if self._state_requires_navigation(state):
                success, next_state = self._follow_flow_state(
                    state,
                    referer=state.current_url or f"{self.AUTH}/about-you",
                )
                if not success:
                    return False, f"navigation failed: {next_state}"
                state = next_state
                self.last_registration_state = state
                continue

            if (
                (not register_submitted)
                and (not otp_verified)
                and (not account_created)
            ):
                self._log(
                    f"Unknown start state, fallback to full registration: {describe_flow_state(state)}"
                )
                state = self._state_from_url(f"{self.AUTH}/create-account/password")
                continue

            return False, f"Unsupported state: {describe_flow_state(state)}"

        return False, "state machine exceeded max steps"
