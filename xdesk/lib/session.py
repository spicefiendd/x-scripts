"""
X session helpers — READ-ONLY.

Auth model (mirrors yahoo-mail-scripts, but prefers CDP cookie export over
launching a second Chrome against a locked profile):

1. Preferred: Chrome already running with --remote-debugging-port (box profiles).
   Export auth_token + ct0 via CDP Network.getAllCookies (Origin suppressed).
2. Fallback: cached .session_cookies.json from a prior refresh.
3. Never print full secrets. Never post/like/follow.

Per-machine Chrome profile path and CDP ports, plus the account's user id and
handle, live in the gitignored lib/account.local.json (see the .example file).
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

# X web-client bearer. Not hardcoded: read from $X_WEB_BEARER, else from the
# gitignored .bearer.txt at the repo root (override path with $X_BEARER_FILE).
BEARER_ENV = "X_WEB_BEARER"

INTERESTING = ("auth_token", "ct0", "twid", "kdt", "guest_id", "personalization_id", "lang")


LOCAL_CONFIG_PATH = Path(
    os.environ.get("X_ACCOUNT_LOCAL", Path(__file__).resolve().parent / "account.local.json")
)
DEFAULT_CDP_PORT = 9222  # generic Chrome default; real ports live in account.local.json

# Keys that are per-user / per-machine and must only ever come from the gitignored
# account.local.json, never from (or be written into) the tracked endpoints.json.
LOCAL_ONLY_KEYS = ("user_rest_id", "screen_name", "cdp_ports", "preferred_profile", "preferred_cdp_port")


def load_local_config() -> dict[str, Any]:
    """Gitignored per-user config (account identifiers, CDP ports, profile path)."""
    if not LOCAL_CONFIG_PATH.exists():
        return {}
    return json.loads(LOCAL_CONFIG_PATH.read_text())


def load_endpoints(include_local: bool = True) -> dict[str, Any]:
    """Tracked endpoints.json, optionally merged with account.local.json.

    Anything that WRITES endpoints.json back to disk must pass include_local=False
    so local account values never leak into the tracked file.
    """
    data = json.loads(ENDPOINTS_PATH.read_text())
    if include_local:
        data.update(load_local_config())
    else:
        for k in LOCAL_ONLY_KEYS:
            data.pop(k, None)
    return data


def get_account() -> dict[str, str]:
    """Return {"user_rest_id", "screen_name", "source"} from account.local.json.

    Raises RuntimeError with setup instructions if either value is missing.
    """
    cfg = load_local_config()
    uid = str(cfg.get("user_rest_id") or "").strip()
    screen = str(cfg.get("screen_name") or "").strip().lstrip("@")
    placeholders = {"", "YOUR_X_USER_REST_ID", "YOUR_HANDLE", "<your_user_id>", "<your_handle>"}
    missing = [k for k, v in (("user_rest_id", uid), ("screen_name", screen)) if v in placeholders]
    if missing:
        raise RuntimeError(
            f"Missing {', '.join(missing)} in {LOCAL_CONFIG_PATH}. Copy "
            f"{LOCAL_CONFIG_PATH.name}.example to {LOCAL_CONFIG_PATH.name} and fill in your "
            "X user id and handle (this file is gitignored)."
        )
    return {"user_rest_id": uid, "screen_name": screen, "source": str(LOCAL_CONFIG_PATH)}


def get_bearer() -> str:
    """Return the X web-client bearer from $X_WEB_BEARER or the gitignored bearer file."""
    env = os.environ.get(BEARER_ENV, "").strip()
    if env:
        return env
    if BEARER_PATH.exists():
        b = BEARER_PATH.read_text().strip()
        if b:
            return b
    raise RuntimeError(
        f"No X web bearer configured. Set ${BEARER_ENV}, or put the bearer on one line in "
        f"{BEARER_PATH} (gitignored; chmod 600). It is the public web-client bearer "
        "embedded in x.com's main.*.js."
    )


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
            "No X session cookies. Start the X-desk Chrome profile (see account.local.json) "
            "signed in to your X account, then run: xdesk refresh-session"
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
    """Write the cookie file mode 0600 from creation (never a world-readable window).

    Writes a 0600 temp file in the same directory (O_CREAT|O_EXCL, mode 0o600 under a
    0o077 umask), fsyncs, then atomically renames it over COOKIES_PATH.
    """
    COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = COOKIES_PATH.with_name(f".{COOKIES_PATH.name}.{os.getpid()}.tmp")
    data = json.dumps(cookies, indent=2).encode()
    old_umask = os.umask(0o077)
    try:
        fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_TRUNC, 0o600)
    finally:
        os.umask(old_umask)
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, COOKIES_PATH)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    return COOKIES_PATH


def _cdp_ports() -> list[int]:
    env = os.environ.get("X_CDP_PORT")
    if env:
        return [int(env)]
    eps = load_local_config()
    ports = [int(eps.get("preferred_cdp_port") or DEFAULT_CDP_PORT)]
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
