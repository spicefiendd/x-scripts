"""
X session helpers — READ-ONLY.

Auth model (mirrors yahoo-mail-scripts, but prefers CDP cookie export over
launching a second Chrome against a locked profile):

1. Preferred: Chrome already running with --remote-debugging-port (box profiles).
   Export auth_token + ct0 via CDP Network.getAllCookies (Origin suppressed).
2. Fallback: cached .session_cookies.json from a prior refresh.
3. Never print full secrets. Never post/like/follow.

Profiles observed signed in as @spicefiendd:
  /home/box/chrome-profile-6  (CDP 9228) — active X desk / trending / search
  /home/box/chrome-profile-2  (CDP 9224) — also has analytics history
"""

from __future__ import annotations

import json
import os
import stat
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]  # /workspace/x-scripts
COOKIES_PATH = Path(os.environ.get("X_SESSION_COOKIES", ROOT / ".session_cookies.json"))
BEARER_PATH = Path(os.environ.get("X_BEARER_FILE", ROOT / ".bearer.txt"))
ENDPOINTS_PATH = Path(__file__).resolve().parent / "endpoints.json"

# Public web-client bearer (same for all X web users). Safe to ship; not a user secret.
DEFAULT_BEARER = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)

INTERESTING = ("auth_token", "ct0", "twid", "kdt", "guest_id", "personalization_id", "lang")


def load_endpoints() -> dict[str, Any]:
    data = json.loads(ENDPOINTS_PATH.read_text())
    local = ENDPOINTS_PATH.parent / "account.local.json"
    if local.exists():
        data.update(json.loads(local.read_text()))
    return data


def get_bearer() -> str:
    if BEARER_PATH.exists():
        b = BEARER_PATH.read_text().strip()
        if b:
            return b
    return DEFAULT_BEARER


def _redact(val: str) -> str:
    if not val:
        return ""
    if len(val) <= 8:
        return "***"
    return f"{val[:3]}…{val[-2:]}(len={len(val)})"


def cookie_status(cookies: dict[str, Any] | None = None) -> dict[str, str]:
    cookies = cookies or load_cookies(refresh=False)
    out = {}
    for k in INTERESTING:
        v = (cookies.get(k) or {}).get("value") if isinstance(cookies.get(k), dict) else cookies.get(k)
        if isinstance(v, dict):
            v = v.get("value")
        out[k] = _redact(v or "")
    return out


def load_cookies(refresh: bool = False) -> dict[str, dict[str, str]]:
    """Return {name: {value, domain}} for X auth cookies."""
    if refresh or not COOKIES_PATH.exists():
        refreshed = refresh_cookies_via_cdp()
        if refreshed:
            return refreshed
    if not COOKIES_PATH.exists():
        raise RuntimeError(
            "No X session cookies. Start the X-desk Chrome profile (chrome-profile-6) "
            "signed in as @spicefiendd, then run: xdesk refresh-session"
        )
    data = json.loads(COOKIES_PATH.read_text())
    # normalize
    out: dict[str, dict[str, str]] = {}
    for k, v in data.items():
        if isinstance(v, dict) and "value" in v:
            out[k] = {"value": v["value"], "domain": v.get("domain", ".x.com")}
        elif isinstance(v, str):
            out[k] = {"value": v, "domain": ".x.com"}
    if "auth_token" not in out or "ct0" not in out:
        raise RuntimeError("Session cookies missing auth_token/ct0 — re-login in Chrome, then refresh_session.py")
    return out


def save_cookies(cookies: dict[str, dict[str, str]]) -> Path:
    COOKIES_PATH.write_text(json.dumps(cookies, indent=2))
    os.chmod(COOKIES_PATH, stat.S_IRUSR | stat.S_IWUSR)
    return COOKIES_PATH


def _cdp_ports() -> list[int]:
    env = os.environ.get("X_CDP_PORT")
    if env:
        return [int(env)]
    eps = load_endpoints()
    ports = [eps.get("preferred_cdp_port", 9228)]
    for p in (eps.get("cdp_ports") or {}).values():
        if p not in ports:
            ports.append(p)
    return ports


def refresh_cookies_via_cdp(port: int | None = None) -> dict[str, dict[str, str]] | None:
    """Pull decrypted cookies from a running Chrome DevTools endpoint."""
    try:
        from websocket import create_connection
    except ImportError as e:
        raise RuntimeError("websocket-client required: pip install websocket-client") from e

    ports = [port] if port else _cdp_ports()
    last_err: Exception | None = None
    for p in ports:
        try:
            cookies = _export_cookies_from_port(p, create_connection)
            if cookies and "auth_token" in cookies and "ct0" in cookies:
                save_cookies(cookies)
                return cookies
        except Exception as e:  # noqa: BLE001 — try next port
            last_err = e
            continue
    if last_err:
        raise RuntimeError(f"CDP cookie refresh failed on ports {ports}: {last_err}") from last_err
    return None


def _export_cookies_from_port(port: int, create_connection) -> dict[str, dict[str, str]]:
    ver = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3))
    # Prefer an existing x.com page target
    targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3))
    pages = [
        t
        for t in targets
        if t.get("type") == "page" and ("x.com" in t.get("url", "") or "twitter.com" in t.get("url", ""))
    ]
    if pages:
        ws_url = pages[0]["webSocketDebuggerUrl"]
    else:
        # Attach to browser and open a target
        ws_url = ver["webSocketDebuggerUrl"]

    ws = create_connection(ws_url, timeout=20, suppress_origin=True)
    msg_id = 0

    def cdp(method, params=None, session_id=None):
        nonlocal msg_id
        msg_id += 1
        payload: dict[str, Any] = {"id": msg_id, "method": method}
        if params is not None:
            payload["params"] = params
        if session_id:
            payload["sessionId"] = session_id
        ws.send(json.dumps(payload))
        while True:
            data = json.loads(ws.recv())
            if data.get("id") == msg_id:
                if "error" in data:
                    raise RuntimeError(data["error"])
                return data.get("result", {})

    session_id = None
    try:
        if not pages:
            # create + attach
            tid = cdp("Target.createTarget", {"url": "https://x.com/home"})["targetId"]
            session_id = cdp("Target.attachToTarget", {"targetId": tid, "flatten": True})["sessionId"]
            cdp("Network.enable", {}, session_id=session_id)
            result = cdp("Network.getAllCookies", {}, session_id=session_id)
        else:
            cdp("Network.enable")
            result = cdp("Network.getAllCookies")
    finally:
        ws.close()

    by_name: dict[str, dict[str, str]] = {}
    for c in result.get("cookies", []):
        if c.get("name") not in INTERESTING:
            continue
        if "x.com" not in c.get("domain", "") and "twitter.com" not in c.get("domain", ""):
            continue
        prev = by_name.get(c["name"])
        if not prev or c.get("domain") == ".x.com":
            by_name[c["name"]] = {"value": c["value"], "domain": c.get("domain", ".x.com")}
    return by_name


def cookie_header(cookies: dict[str, dict[str, str]] | None = None) -> str:
    cookies = cookies or load_cookies()
    parts = []
    for name in ("auth_token", "ct0", "twid", "kdt", "guest_id", "personalization_id", "lang"):
        if name in cookies:
            parts.append(f"{name}={cookies[name]['value']}")
    return "; ".join(parts)


def auth_headers(cookies: dict[str, dict[str, str]] | None = None) -> dict[str, str]:
    cookies = cookies or load_cookies()
    ct0 = cookies["ct0"]["value"]
    return {
        "authorization": f"Bearer {get_bearer()}",
        "x-csrf-token": ct0,
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-active-user": "yes",
        "x-twitter-client-language": "en",
        "content-type": "application/json",
        "user-agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
        ),
        "cookie": cookie_header(cookies),
    }
