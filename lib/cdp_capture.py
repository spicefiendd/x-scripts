"""
CDP helpers for read-only X GraphQL capture via chrome-profile-6 (port 9228).

Prefer reusing an existing x.com tab. If we must create a tab, close it when done.
Never posts / likes / follows.
"""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlparse

from .session import ENDPOINTS_PATH, _cdp_ports, load_endpoints

ROOT = Path(__file__).resolve().parent.parent


class CdpError(RuntimeError):
    pass


def cdp_available(port: int | None = None) -> tuple[bool, int | None, str]:
    """Return (ok, port, detail)."""
    ports = [port] if port else _cdp_ports()
    last = "no ports"
    for p in ports:
        try:
            ver = json.load(urllib.request.urlopen(f"http://127.0.0.1:{p}/json/version", timeout=2))
            return True, p, ver.get("Browser", f"port {p}")
        except Exception as e:  # noqa: BLE001
            last = str(e)
    return False, None, last


def _list_targets(port: int) -> list[dict[str, Any]]:
    return json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3))


def _pick_x_page(targets: list[dict[str, Any]]) -> dict[str, Any] | None:
    pages = [t for t in targets if t.get("type") == "page"]
    x_pages = [
        t
        for t in pages
        if "x.com" in t.get("url", "") or "twitter.com" in t.get("url", "")
    ]
    if not x_pages:
        return None
    # Prefer an existing search tab, else any x.com page
    for t in x_pages:
        if "/search" in t.get("url", ""):
            return t
    return x_pages[0]


class CdpSession:
    """Thin CDP websocket session bound to one page target."""

    def __init__(self, ws_url: str, timeout: float = 120):
        try:
            from websocket import create_connection
        except ImportError as e:
            raise CdpError("websocket-client required: pip install websocket-client") from e
        self.ws = create_connection(ws_url, timeout=timeout, suppress_origin=True)
        self.msg_id = 0
        self._inbox: list[dict[str, Any]] = []

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass

    def call(self, method: str, params: dict | None = None, timeout: float = 45) -> dict[str, Any]:
        self.msg_id += 1
        mid = self.msg_id
        payload: dict[str, Any] = {"id": mid, "method": method}
        if params is not None:
            payload["params"] = params
        self.ws.send(json.dumps(payload))
        deadline = time.time() + timeout
        while time.time() < deadline:
            # drain previously buffered events first for matching id? we only buffer events
            data = self._recv(timeout=max(0.5, min(5.0, deadline - time.time())))
            if data is None:
                continue
            if data.get("id") == mid:
                if "error" in data:
                    raise CdpError(f"CDP {method}: {data['error']}")
                return data.get("result") or {}
            # event — keep for pollers
            if data.get("method"):
                self._inbox.append(data)
        raise CdpError(f"CDP timeout waiting for {method}")

    def _recv(self, timeout: float) -> dict[str, Any] | None:
        try:
            self.ws.settimeout(timeout)
            return json.loads(self.ws.recv())
        except Exception:  # noqa: BLE001 — timeout / empty
            return None

    def poll_events(self, until: float) -> list[dict[str, Any]]:
        """Recv until deadline; return events (and leave unmatched responses in inbox logic)."""
        out: list[dict[str, Any]] = []
        # flush inbox
        if self._inbox:
            out.extend(self._inbox)
            self._inbox.clear()
        while time.time() < until:
            data = self._recv(timeout=max(0.2, min(1.0, until - time.time())))
            if data is None:
                continue
            if data.get("method"):
                out.append(data)
            elif data.get("id") is not None:
                # unexpected late response — ignore
                continue
        return out


def _create_page_target(port: int, url: str) -> dict[str, Any]:
    """Create a new page via /json/new and return its target dict."""
    # Chrome accepts PUT /json/new?{url}
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/json/new?{quote(url, safe='')}",
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def _close_target(port: int, target_id: str) -> None:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/json/close/{target_id}", timeout=5).read()
    except Exception:  # noqa: BLE001
        pass


def search_url(query: str, product: str = "Top") -> str:
    f = "top" if product.lower() == "top" else "live"
    return f"https://x.com/search?q={quote(query)}&src=typed_query&f={f}"


def capture_search_timeline(
    query: str,
    product: str = "Top",
    *,
    port: int | None = None,
    timeout_s: float = 20,
    count_hint: int = 20,
) -> dict[str, Any]:
    """
    Navigate an x.com tab to search and capture SearchTimeline GraphQL JSON body.

    Returns dict with keys: raw (parsed JSON), url, query_id, source='cdp', port,
    created_tab (bool).
    """
    ok, p, detail = cdp_available(port)
    if not ok or p is None:
        raise CdpError(f"CDP not available ({detail}). Start chrome-profile-6 with --remote-debugging-port=9228")

    targets = _list_targets(p)
    page = _pick_x_page(targets)
    created_tab = False
    target_id: str | None = None
    url = search_url(query, product)

    if page is None:
        page = _create_page_target(p, "https://x.com/home")
        created_tab = True
        target_id = page.get("id")
        # refresh list for websocket url
        time.sleep(0.8)
        targets = _list_targets(p)
        page = next((t for t in targets if t.get("id") == target_id), page)

    ws_url = page["webSocketDebuggerUrl"]
    target_id = page.get("id") or target_id
    sess = CdpSession(ws_url)
    pending: dict[str, dict[str, Any]] = {}
    captured: dict[str, Any] | None = None
    captured_url: str | None = None
    query_id: str | None = None

    try:
        sess.call("Network.enable")
        sess.call("Page.enable")
        sess.call("Page.navigate", {"url": url})
        deadline = time.time() + timeout_s
        while time.time() < deadline and captured is None:
            for data in sess.poll_events(until=min(time.time() + 1.0, deadline)):
                m = data.get("method")
                if m == "Network.requestWillBeSent":
                    req = data["params"]["request"]
                    req_url = req.get("url") or ""
                    if "SearchTimeline" in req_url:
                        rid = data["params"]["requestId"]
                        pending[rid] = {"url": req_url}
                        # extract queryId from path /i/api/graphql/{qid}/SearchTimeline
                        m_qid = re.search(r"/graphql/([^/]+)/SearchTimeline", req_url)
                        if m_qid:
                            query_id = m_qid.group(1)
                elif m == "Network.loadingFinished":
                    rid = data["params"]["requestId"]
                    if rid not in pending:
                        continue
                    body_r = sess.call("Network.getResponseBody", {"requestId": rid}, timeout=30)
                    body = body_r.get("body") or ""
                    if body_r.get("base64Encoded"):
                        body = base64.b64decode(body).decode("utf-8", errors="replace")
                    if not body.strip():
                        continue
                    try:
                        captured = json.loads(body)
                    except json.JSONDecodeError as e:
                        raise CdpError(f"SearchTimeline body not JSON: {e}") from e
                    captured_url = pending[rid]["url"]
                    break
        if captured is None:
            raise CdpError(
                f"No SearchTimeline response captured within {timeout_s}s for {query!r}. "
                "Is the profile signed in as @spicefiendd?"
            )
        # Optionally persist updated queryId
        if query_id:
            _maybe_update_queryid("SearchTimeline", query_id)
        return {
            "raw": captured,
            "url": captured_url,
            "search_page": url,
            "query_id": query_id,
            "source": "cdp",
            "port": p,
            "created_tab": created_tab,
            "count_hint": count_hint,
        }
    finally:
        sess.close()
        if created_tab and target_id:
            _close_target(p, target_id)


def _maybe_update_queryid(operation: str, query_id: str) -> bool:
    eps = load_endpoints()
    gql = eps.setdefault("graphql", {})
    if gql.get(operation) == query_id:
        return False
    gql[operation] = query_id
    ENDPOINTS_PATH.write_text(json.dumps(eps, indent=2) + "\n")
    return True


def repair_queryids(
    *,
    port: int | None = None,
    timeout_s: float = 25,
) -> dict[str, Any]:
    """
    CDP-navigate explore + search, re-capture GraphQL queryIds into lib/endpoints.json.
    """
    ok, p, detail = cdp_available(port)
    if not ok or p is None:
        raise CdpError(f"CDP not available ({detail})")

    targets = _list_targets(p)
    page = _pick_x_page(targets)
    created_tab = False
    target_id: str | None = None
    if page is None:
        page = _create_page_target(p, "https://x.com/home")
        created_tab = True
        target_id = page.get("id")
        time.sleep(0.8)
        targets = _list_targets(p)
        page = next((t for t in targets if t.get("id") == target_id), page)

    wanted = (
        "SearchTimeline",
        "GenericTimelineById",
        "ExplorePage",
        "accountOverviewDailyQuery",
        "contentPageQuery",
    )
    found: dict[str, str] = {}
    features_blob: dict[str, Any] | None = None

    sess = CdpSession(page["webSocketDebuggerUrl"])
    try:
        sess.call("Network.enable")
        sess.call("Page.enable")

        def harvest(until: float) -> None:
            nonlocal features_blob
            for data in sess.poll_events(until=until):
                if data.get("method") != "Network.requestWillBeSent":
                    continue
                req_url = data["params"]["request"].get("url") or ""
                if "/i/api/graphql/" not in req_url:
                    continue
                m = re.search(r"/graphql/([^/]+)/([^/?]+)", req_url)
                if not m:
                    continue
                qid, op = m.group(1), m.group(2)
                if op in wanted and op not in found:
                    found[op] = qid
                    if op == "SearchTimeline" and features_blob is None:
                        qs = parse_qs(urlparse(req_url).query)
                        if "features" in qs:
                            try:
                                features_blob = json.loads(qs["features"][0])
                            except json.JSONDecodeError:
                                pass

        # Explore / trending
        sess.call("Page.navigate", {"url": "https://x.com/explore/tabs/trending"})
        harvest(time.time() + timeout_s / 2)
        # Search top
        sess.call("Page.navigate", {"url": search_url("X", "Top")})
        harvest(time.time() + timeout_s / 2)

        eps = load_endpoints()
        gql = eps.setdefault("graphql", {})
        updated: dict[str, dict[str, str]] = {}
        for op, qid in found.items():
            old = gql.get(op)
            if old != qid:
                updated[op] = {"old": old or "", "new": qid}
            gql[op] = qid
        ENDPOINTS_PATH.write_text(json.dumps(eps, indent=2) + "\n")

        features_updated = False
        if features_blob:
            feat_path = Path(__file__).resolve().parent / "features.json"
            old_feat = json.loads(feat_path.read_text()) if feat_path.exists() else {}
            if old_feat != features_blob:
                feat_path.write_text(json.dumps(features_blob, indent=2) + "\n")
                features_updated = True

        return {
            "port": p,
            "found": found,
            "updated": updated,
            "features_updated": features_updated,
            "endpoints_path": str(ENDPOINTS_PATH),
            "created_tab": created_tab,
        }
    finally:
        sess.close()
        if created_tab and target_id:
            _close_target(p, target_id)


def parse_search_tweets(payload: dict[str, Any], limit: int = 8) -> list[dict[str, Any]]:
    """Parse SearchTimeline (or similar) into desk-friendly tweet dicts."""
    hits: list[dict[str, Any]] = []

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            typename = o.get("__typename")
            if typename == "Tweet" and "legacy" in o:
                leg = o["legacy"]
                user_res = (((o.get("core") or {}).get("user_results") or {}).get("result") or {})
                # handle User / UserUnavailable wrappers
                if user_res.get("__typename") == "User" or "legacy" in user_res or "core" in user_res:
                    core = user_res.get("core") or {}
                    uleg = user_res.get("legacy") or {}
                    screen = core.get("screen_name") or uleg.get("screen_name")
                else:
                    screen = None
                tid = o.get("rest_id") or leg.get("id_str")
                views = None
                v = o.get("views")
                if isinstance(v, dict) and v.get("count") is not None:
                    try:
                        views = int(v["count"])
                    except (TypeError, ValueError):
                        views = v.get("count")
                text = leg.get("full_text") or ""
                hits.append(
                    {
                        "id": tid,
                        "author": screen,
                        "screen_name": screen,
                        "hook": text.replace("\n", " ").strip()[:140],
                        "full_text": text,
                        "likes": leg.get("favorite_count"),
                        "retweets": leg.get("retweet_count"),
                        "replies": leg.get("reply_count"),
                        "quotes": leg.get("quote_count"),
                        "views": views,
                        "created_at": leg.get("created_at"),
                        "url": f"https://x.com/{screen}/status/{tid}" if screen and tid else None,
                    }
                )
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(payload)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for h in hits:
        hid = str(h.get("id") or "")
        if not hid or hid in seen:
            continue
        seen.add(hid)
        out.append(h)
    # Prefer engagement for "why trending" narrative
    out.sort(
        key=lambda x: (
            int(x.get("views") or 0),
            int(x.get("likes") or 0),
            int(x.get("retweets") or 0),
        ),
        reverse=True,
    )
    return out[:limit]
