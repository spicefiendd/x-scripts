#!/usr/bin/env python3
"""
x_analytics.py — Account analytics overview + top posts for @spicefiendd.
Replaces browserUse on https://x.com/i/account_analytics (and content tab).
READ-ONLY. Never posts.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.session import load_cookies, load_endpoints  # noqa: E402
from lib.x_api import (  # noqa: E402
    fetch_account_overview,
    fetch_content_page,
    summarize_overview,
)

OUT_DIR = Path(__file__).resolve().parent / "out"


def _tweet_status_id(tw_id: str | None) -> str | None:
    if not tw_id:
        return None
    try:
        pad = "=" * (-len(tw_id) % 4)
        raw = base64.b64decode(tw_id + pad).decode("utf-8", errors="replace")
        # Tweet:2096...
        if ":" in raw:
            return raw.split(":", 1)[1]
    except Exception:
        return None
    return None


def enrich_posts(payload: dict, limit: int) -> list[dict]:
    """Re-parse with status ids from raw payload."""
    posts = []
    user = payload["data"]["viewer_v2"]["user_results"]["result"]
    screen = (user.get("core") or {}).get("screen_name") or load_endpoints().get("screen_name")
    for tr in user.get("tweets_results") or []:
        tw = (tr or {}).get("result") or {}
        if tw.get("__typename") != "Tweet":
            continue
        details = tw.get("details") or {}
        metrics = {}
        for m in tw.get("organic_metrics_total") or []:
            if isinstance(m, dict) and m.get("metric_type"):
                metrics[m["metric_type"]] = int(m.get("metric_value") or 0)
        status_id = tw.get("rest_id") or _tweet_status_id(tw.get("id"))
        posts.append(
            {
                "status_id": status_id,
                "url": f"https://x.com/{screen}/status/{status_id}" if status_id else None,
                "text": details.get("full_text") or "",
                "created_at_ms": details.get("created_at_ms"),
                "metrics": metrics,
                "impressions": metrics.get("Impressions", 0),
                "likes": metrics.get("Likes", 0),
                "engagements": metrics.get("Engagements", 0),
                "replies": metrics.get("Replies", 0),
                "retweets": metrics.get("Retweets", 0),
                "profile_visits": metrics.get("ProfileVisits", 0),
                "screen_name": screen,
            }
        )
    posts.sort(key=lambda p: (p["impressions"], p["engagements"]), reverse=True)
    return posts[:limit]


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch X account analytics (read-only)")
    ap.add_argument("--days", type=int, default=7, help="Content window (default 7)")
    ap.add_argument("--overview-days", type=int, default=28, help="Overview comparison window")
    ap.add_argument("--also-28d-content", action="store_true", help="Also fetch 28d top posts")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--refresh-session", action="store_true")
    ap.add_argument("--json-only", action="store_true")
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()

    load_cookies(refresh=args.refresh_session)

    overview_raw = fetch_account_overview(days=args.overview_days)
    overview = summarize_overview(overview_raw, days=args.overview_days)
    # Also compute 7d slice from same series when overview_days>=7
    overview_7 = summarize_overview(overview_raw, days=min(7, args.overview_days))

    content_raw = fetch_content_page(days=args.days)
    top_posts = enrich_posts(content_raw, args.top)

    content_28 = None
    top_28 = None
    if args.also_28d_content or args.days != 28:
        if args.also_28d_content:
            content_28 = fetch_content_page(days=28)
            top_28 = enrich_posts(content_28, args.top)

    OUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = Path(args.out) if args.out else OUT_DIR / f"analytics-{stamp}.json"
    payload = {
        "fetched_at_utc": stamp,
        "account": load_endpoints().get("screen_name"),
        "overview_28d": overview,
        "overview_7d_slice": overview_7,
        "top_posts_days": args.days,
        "top_posts": top_posts,
        "top_posts_28d": top_28,
        "raw": {
            "overview": overview_raw,
            "content": content_raw,
            "content_28": content_28,
        },
    }
    out_path.write_text(json.dumps(payload, indent=2))

    if args.json_only:
        slim = {k: v for k, v in payload.items() if k != "raw"}
        slim["out"] = str(out_path)
        print(json.dumps(slim, indent=2))
        return 0

    print(f"@spicefiendd analytics — wrote {out_path}")
    print(f"Followers: {overview.get('followers')} (verified: {overview.get('verified_followers')})")
    print(f"Overview window {overview.get('days')}d — follows +{overview.get('follows')} / -{overview.get('unfollows')} (net {overview.get('net_follows')})")
    print(f"Displayed (impressions-ish) {overview.get('days')}d: {overview.get('impressions_displayed')}")
    print(f"7d slice Displayed: {overview_7.get('impressions_displayed')}  net follows: {overview_7.get('net_follows')}")
    print(f"\nTop posts last {args.days}d:")
    for i, p in enumerate(top_posts, 1):
        text = (p.get("text") or "").replace("\n", " ")
        if len(text) > 90:
            text = text[:87] + "..."
        print(
            f"{i:>2}. imp={p['impressions']:<6} eng={p['engagements']:<4} ❤{p['likes']:<3} "
            f"{text}"
        )
        if p.get("url"):
            print(f"    {p['url']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PermissionError as e:
        print(str(e), file=sys.stderr)
        raise SystemExit(2)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(1)
