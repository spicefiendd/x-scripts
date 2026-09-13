#!/usr/bin/env python3
"""
x_trend_why.py / x-trend-why — explain WHY a topic is trending on X.

READ-ONLY. Prefers CDP Network capture of SearchTimeline (chrome-profile-6 :9228)
because plain requests SearchTimeline often 404 without live x-client-transaction-id.
Falls back to cookie-based GraphQL GET if CDP is down.

Never posts / likes / follows / DMs. Never prints full auth_token/ct0.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.cdp_capture import (  # noqa: E402
    CdpError,
    capture_search_timeline,
    cdp_available,
    parse_search_tweets,
    repair_queryids,
)
from lib.session import load_cookies  # noqa: E402
from lib.x_api import fetch_trending, parse_trends  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "out"


def _slug(q: str) -> str:
    s = re.sub(r"[^\w\-]+", "-", q.strip(), flags=re.UNICODE).strip("-").lower()
    return (s or "query")[:48]


def _match_trend(query: str, trends: list[dict]) -> dict | None:
    q = query.strip().lstrip("#").lower()
    if not q:
        return None
    # exact then substring
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
    """Fallback SearchTimeline via plain requests (often 404)."""
    from lib.x_api import _get

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
    """
    Returns (raw_payload, posts, source_label).
    source_label is 'cdp' or 'requests'.
    """
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
        "signed in as @spicefiendd, then: .venv/bin/python refresh_session.py --port 9228"
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


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Explain why a trend/query is moving on X (read-only CDP capture)"
    )
    ap.add_argument("query", nargs="?", help="Trend name or search query")
    ap.add_argument("--count", type=int, default=8)
    ap.add_argument("--product", choices=["Top", "Latest"], default="Top")
    ap.add_argument(
        "--refresh-session",
        action="store_true",
        help="Export cookies via CDP from chrome-profile-6 (port 9228 preferred)",
    )
    ap.add_argument("--json-only", action="store_true")
    ap.add_argument("--out", type=str, default=None)
    ap.add_argument(
        "--repair-queryids",
        action="store_true",
        help="CDP-navigate explore+search and rewrite lib/endpoints.json queryIds, then exit",
    )
    ap.add_argument(
        "--no-cdp",
        action="store_true",
        help="Skip CDP; try requests SearchTimeline only (often 404)",
    )
    ap.add_argument(
        "--skip-trend-meta",
        action="store_true",
        help="Do not call GenericTimelineById for trend metadata",
    )
    args = ap.parse_args()

    if args.repair_queryids:
        result = repair_queryids()
        print(json.dumps(result, indent=2) if args.json_only else (
            f"Repaired queryIds on CDP {result['port']}\n"
            f"  found: {result['found']}\n"
            f"  updated: {result['updated'] or '(none changed)'}\n"
            f"  features_updated: {result['features_updated']}\n"
            f"  wrote {result['endpoints_path']}"
        ))
        return 0

    if not args.query:
        ap.error("query is required unless --repair-queryids")

    if args.refresh_session:
        load_cookies(refresh=True)

    # Trend metadata (optional; uses requests GenericTimelineById which usually works)
    trend_meta = None
    trends: list[dict] = []
    if not args.skip_trend_meta:
        try:
            load_cookies(refresh=False)
            trends = parse_trends(fetch_trending(count=30))
            trend_meta = _match_trend(args.query, trends)
        except Exception as e:  # noqa: BLE001
            trends = []
            trend_meta = None
            trend_err = str(e)
        else:
            trend_err = None
    else:
        trend_err = None

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
        # omit full raw by default size — keep compact; include under "raw" only if small
        "raw_omitted": True,
    }
    # Keep a compact raw pointer: tweet count only; full raw available if --out and small
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


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CdpError as e:
        print(f"CDP ERROR: {e}", file=sys.stderr)
        raise SystemExit(3)
    except PermissionError as e:
        print(str(e), file=sys.stderr)
        raise SystemExit(2)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(1)
