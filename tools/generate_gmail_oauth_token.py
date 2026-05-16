import json
import secrets
import sys
import urllib.parse
import webbrowser
from pathlib import Path

import requests

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def load_credentials(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload.get("installed"), dict):
        payload = payload["installed"]
    return payload


def build_auth_url(credentials: dict, state: str) -> str:
    params = {
        "client_id": credentials["client_id"],
        "redirect_uri": credentials.get("redirect_uris", ["http://localhost"])[0],
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{credentials.get('auth_uri', 'https://accounts.google.com/o/oauth2/auth')}?{urllib.parse.urlencode(params)}"


def exchange_code(credentials: dict, code: str) -> dict:
    resp = requests.post(
        credentials.get("token_uri", "https://oauth2.googleapis.com/token"),
        data={
            "code": code,
            "client_id": credentials["client_id"],
            "client_secret": credentials["client_secret"],
            "redirect_uri": credentials.get("redirect_uris", ["http://localhost"])[0],
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python tools/generate_gmail_oauth_token.py <credentials.json>")
        return 1

    credentials_path = Path(sys.argv[1]).expanduser().resolve()
    credentials = load_credentials(credentials_path)
    state = secrets.token_urlsafe(16)
    auth_url = build_auth_url(credentials, state)

    print("Open this URL in your browser and approve Gmail access:")
    print(auth_url)
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    print("
After approval, Google will redirect to something like http://localhost/?code=...&scope=...")
    redirect_url = input("Paste the full redirected URL here: ").strip()
    parsed = urllib.parse.urlparse(redirect_url)
    query = urllib.parse.parse_qs(parsed.query)
    code = (query.get("code") or [""])[0].strip()
    returned_state = (query.get("state") or [""])[0].strip()
    if not code:
        print("No code found in redirected URL")
        return 2
    if returned_state and returned_state != state:
        print("State mismatch; aborting for safety")
        return 3

    token = exchange_code(credentials, code)
    print("
Token JSON:")
    print(json.dumps(token, ensure_ascii=False, indent=2))
    token_path = credentials_path.with_name("gmail_token.json")
    token_path.write_text(json.dumps(token, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"
Saved token to: {token_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
