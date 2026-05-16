"""HTTP client helpers for ChatGPT/OpenAI flows."""

import json
import logging
from typing import Any, Dict, Optional, Tuple

from curl_cffi import requests as cffi_requests

from core.http_client import HTTPClient, HTTPClientError, RequestConfig

logger = logging.getLogger(__name__)


class OpenAIHTTPClient(HTTPClient):
    """HTTP client with OpenAI-specific defaults."""

    def __init__(
        self,
        proxy_url: Optional[str] = None,
        config: Optional[RequestConfig] = None,
    ):
        super().__init__(proxy_url, config)

        if config is None:
            self.config.timeout = 30
            self.config.max_retries = 3

        self.default_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/136.0.7103.92 Safari/537.36"
            ),
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
        }

    def check_ip_location(self) -> Tuple[bool, Optional[str]]:
        """
        Return whether the proxy geo is acceptable for registration.

        `False` is reserved for explicit blocked geos only.
        Missing trace data or transient trace failures are treated as
        unknown geo and allowed to continue.
        """
        try:
            response = self.get("https://cloudflare.com/cdn-cgi/trace", timeout=10)
            trace_text = response.text

            import re

            loc_match = re.search(r"loc=([A-Z]+)", trace_text)
            loc = loc_match.group(1) if loc_match else None

            if loc in ["CN", "HK", "MO", "TW"]:
                return False, loc
            if not loc:
                logger.warning("IP location trace missing loc field; continue without geo hint")
            return True, loc
        except Exception as exc:
            logger.warning(f"IP location trace failed, continue without geo hint: {exc}")
            return True, None

    def send_openai_request(
        self,
        endpoint: str,
        method: str = "POST",
        data: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Send an OpenAI API request and decode the JSON response."""
        request_headers = self.default_headers.copy()
        if headers:
            request_headers.update(headers)

        if json_data is not None and "Content-Type" not in request_headers:
            request_headers["Content-Type"] = "application/json"
        elif data is not None and "Content-Type" not in request_headers:
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"

        try:
            response = self.request(
                method,
                endpoint,
                data=data,
                json=json_data,
                headers=request_headers,
                **kwargs,
            )
            response.raise_for_status()

            try:
                return response.json()
            except json.JSONDecodeError:
                return {"raw_response": response.text}
        except cffi_requests.RequestsError as exc:
            raise HTTPClientError(f"OpenAI request failed: {endpoint} - {exc}")

    def check_sentinel(self, did: str, proxies: Optional[Dict] = None) -> Optional[str]:
        """Fetch the sentinel token used by authorize flows."""
        from .constants import OPENAI_API_ENDPOINTS

        try:
            sen_req_body = f'{{"p":"","id":"{did}","flow":"authorize_continue"}}'

            response = self.post(
                OPENAI_API_ENDPOINTS["sentinel"],
                headers={
                    "origin": "https://sentinel.openai.com",
                    "referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6",
                    "content-type": "text/plain;charset=UTF-8",
                },
                data=sen_req_body,
            )

            if response.status_code == 200:
                return response.json().get("token")

            logger.warning("Sentinel check failed: %s", response.status_code)
            return None
        except Exception as exc:
            logger.error("Sentinel check error: %s", exc)
            return None


def create_http_client(
    proxy_url: Optional[str] = None,
    config: Optional[RequestConfig] = None,
) -> HTTPClient:
    """Create a generic HTTP client."""
    return HTTPClient(proxy_url, config)


def create_openai_client(
    proxy_url: Optional[str] = None,
    config: Optional[RequestConfig] = None,
) -> OpenAIHTTPClient:
    """Create an OpenAI HTTP client."""
    return OpenAIHTTPClient(proxy_url, config)
