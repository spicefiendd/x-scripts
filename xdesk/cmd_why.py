"""xdesk why — explain WHY a topic is trending. READ-ONLY."""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from xdesk.lib.cdp_capture import (
    CdpError,
    capture_search_timeline,
    cdp_available,
    parse_search_tweets,
)
from xdesk.lib.session import load_cookies
from xdesk.lib.x_api import fetch_trending, parse_trends
from xdesk.paths import OUT_DIR


def _slug(q: str) -> str:
    s = re.sub(r"[^\w\-]+", "-", q.strip(), flags=re.UNICODE).strip("-").lower()
    return (s or "query")[:48]


def _match_trend(query: str, trends: list[dict]) -> dict | None:
    q = query.strip().lstrip("#").lower()
    if not q:
        return None
    for t in trends:
        name = (t.get("name") or "").lstrip("#").lower()
        if name == q:
            return t
    for t in trends:
        name = (t.get("name") or "").lstrip("#").lower()
        if q in name or name in q:
            return t
    return None


def _fetch_via_requests(query: str, count: int, product: str) -> dict:
    from xdesk.lib.x_api import _get

    variables = {
        "rawQuery": query,
        "count": count,
        "querySource": "typed_query",
        "product": product,
        "withGrokTranslatedBio": False,
        "withQuickPromoteEligibilityTweetFields": False,
    }
    return _get("SearchTimeline", variables)


def fetch_posts(
    query: str,
    count: int,
    product: str,
    *,
    prefer_cdp: bool = True,
) -> tuple[dict, list[dict], str]:
    errors: list[str] = []
    if prefer_cdp:
        ok, port, detail = cdp_available()
        if ok:
            try:
                cap = capture_search_timeline(
                    query, product=product, port=port, count_hint=count
                )
                posts = parse_search_tweets(cap["raw"], limit=count)
                return cap["raw"], posts, "cdp"
            except Exception as e:  # noqa: BLE001
                errors.append(f"CDP capture failed: {e}")
        else:
            errors.append(f"CDP down: {detail}")

    try:
        load_cookies(refresh=False)
        raw = _fetch_via_requests(query, count=count, product=product)
        posts = parse_search_tweets(raw, limit=count)
        return raw, posts, "requests"
    except Exception as e:  # noqa: BLE001
        errors.append(f"requests SearchTimeline failed: {e}")

    msg = (
        "Could not fetch SearchTimeline via CDP or requests.\n"
        + "\n".join(f"- {e}" for e in errors)
        + "\nFix: ensure chrome-profile-6 is running with --remote-debugging-port=9228, "
        "signed in as @spicefiendd, then: xdesk refresh-session --port 9228"
    )
    raise RuntimeError(msg)


def explain_why(query: str, trend: dict | None, posts: list[dict]) -> str:
    lines: list[str] = []
    lines.append(f"Why “{query}” is moving on X")
    if trend:
        bits = []
        if trend.get("rank"):
            bits.append(f"US trend #{trend['rank']}")
        if trend.get("description"):
            bits.append(str(trend["description"]))
        elif trend.get("context"):
            bits.append(str(trend["context"]))
        if trend.get("grouped"):
            bits.append("also: " + ", ".join(trend["grouped"][:4]))
        if bits:
            lines.append("Trend: " + " — ".join(bits))
        else:
            lines.append(f"Trend match: {trend.get('name')}")
    else:
        lines.append("No exact match in current US trending list (still skimming Top posts).")

    if not posts:
        lines.append("No posts captured — cannot explain hooks.")
        return "\n".join(lines)

    lines.append(f"Top hooks ({len(posts)}):")
    for i, p in enumerate(posts, 1):
        author = p.get("author") or "?"
        hook = (p.get("hook") or "").strip()
        stats = []
        if p.get("views") is not None:
            stats.append(f"👁{p['views']}")
        if p.get("likes") is not None:
            stats.append(f"❤{p['likes']}")
        if p.get("retweets") is not None:
            stats.append(f"↻{p['retweets']}")
        stat_s = " ".join(stats)
        lines.append(f"  {i}. @{author} {stat_s} — {hook}")
    return "\n".join(lines)


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("why", help="Explain why a topic is moving on X (read-only)")
    p.add_argument("query", help="Trend name or search query")
    p.add_argument("--count", type=int, default=8)
    p.add_argument("--product", choices=["Top", "Latest"], default="Top")
    p.add_argument(
        "--refresh-session",
        action="store_true",
        help="Export cookies via CDP from chrome-profile-6 (port 9228 preferred)",
    )
    p.add_argument("--json-only", action="store_true")
    p.add_argument("--out", type=str, default=None)
    p.add_argument(
        "--no-cdp",
        action="store_true",
        help="Skip CDP; try requests SearchTimeline only (often 404)",
    )
    p.add_argument(
        "--skip-trend-meta",
        action="store_true",
        help="Do not call GenericTimelineById for trend metadata",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    if args.refresh_session:
        load_cookies(refresh=True)

    trend_meta = None
    trend_err = None
    if not args.skip_trend_meta:
        try:
            load_cookies(refresh=False)
            trends = parse_trends(fetch_trending(count=30))
            trend_meta = _match_trend(args.query, trends)
        except Exception as e:  # noqa: BLE001
            trend_meta = None
            trend_err = str(e)

    raw, posts, source = fetch_posts(
        args.query,
        count=args.count,
        product=args.product,
        prefer_cdp=not args.no_cdp,
    )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    OUT_DIR.mkdir(exist_ok=True)
    out_path = Path(args.out) if args.out else OUT_DIR / f"trend-why-{_slug(args.query)}-{stamp}.json"

    narrative = explain_why(args.query, trend_meta, posts)
    payload = {
        "fetched_at_utc": stamp,
        "query": args.query,
        "product": args.product,
        "source": source,
        "count": len(posts),
        "trend": trend_meta,
        "trend_lookup_error": trend_err,
        "posts": posts,
        "why": narrative,
        "raw_omitted": True,
    }
    if len(json.dumps(raw)) < 2_000_000:
        payload["raw"] = raw
        payload["raw_omitted"] = False

    out_path.write_text(json.dumps(payload, indent=2))

    if args.json_only:
        slim = {
            "query": args.query,
            "product": args.product,
            "source": source,
            "trend": trend_meta,
            "posts": posts,
            "why": narrative,
            "out": str(out_path),
        }
        print(json.dumps(slim, indent=2))
    else:
        print(narrative)
        print(f"\n[{source}] wrote {out_path}")
    return 0


# Re-export for cli error handling
__all__ = ["add_parser", "run", "CdpError"]
