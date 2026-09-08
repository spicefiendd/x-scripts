#!/usr/bin/env python3
"""
x_digest.py — Thin Sunday weekly analytics digest for Dom / @spicefiendd desk.
Combines 7d + 28d overview and top posts into a short text digest + JSON.
READ-ONLY.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.session import load_cookies, load_endpoints  # noqa: E402
from lib.x_api import fetch_account_overview, fetch_content_page, summarize_overview  # noqa: E402
from x_analytics import enrich_posts  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "out"
ET = ZoneInfo("America/Indiana/Indianapolis")


def main() -> int:
    ap = argparse.ArgumentParser(description="Weekly X analytics digest (read-only)")
    ap.add_argument("--refresh-session", action="store_true")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()

    load_cookies(refresh=args.refresh_session)
    screen = load_endpoints().get("screen_name", "account")

    overview_raw = fetch_account_overview(days=28)
    o28 = summarize_overview(overview_raw, days=28)
    o7 = summarize_overview(overview_raw, days=7)
    c7 = enrich_posts(fetch_content_page(days=7), args.top)
    c28 = enrich_posts(fetch_content_page(days=28), args.top)

    now_et = datetime.now(ET)
    lines = [
        f"X weekly digest — @{screen}",
        f"Generated: {now_et.strftime('%Y-%m-%d %H:%M %Z')}",
        "",
        f"Followers: {o28.get('followers')} (verified {o28.get('verified_followers')})",
        f"7d:  Displayed≈{o7.get('impressions_displayed')}  follows +{o7.get('follows')}/-{o7.get('unfollows')} (net {o7.get('net_follows')})",
        f"28d: Displayed≈{o28.get('impressions_displayed')}  follows +{o28.get('follows')}/-{o28.get('unfollows')} (net {o28.get('net_follows')})",
        "",
        "Top posts (7d):",
    ]
    for i, p in enumerate(c7, 1):
        text = (p.get("text") or "").replace("\n", " ")
        if len(text) > 100:
            text = text[:97] + "..."
        lines.append(f"  {i}. imp={p['impressions']} eng={p['engagements']} — {text}")
        if p.get("url"):
            lines.append(f"     {p['url']}")
    lines.append("")
    lines.append("Top posts (28d):")
    for i, p in enumerate(c28, 1):
        text = (p.get("text") or "").replace("\n", " ")
        if len(text) > 100:
            text = text[:97] + "..."
        lines.append(f"  {i}. imp={p['impressions']} eng={p['engagements']} — {text}")

    digest_text = "\n".join(lines) + "\n"
    OUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = Path(args.out) if args.out else OUT_DIR / f"digest-{stamp}.txt"
    json_path = out_path.with_suffix(".json")
    out_path.write_text(digest_text)
    json_path.write_text(
        json.dumps(
            {
                "fetched_at_utc": stamp,
                "account": screen,
                "overview_7d": o7,
                "overview_28d": o28,
                "top_7d": c7,
                "top_28d": c28,
                "digest_text": digest_text,
            },
            indent=2,
        )
    )
    print(digest_text)
    print(f"(wrote {out_path} and {json_path})")
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
