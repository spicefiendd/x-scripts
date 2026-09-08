"""xdesk digest — Sunday weekly analytics digest. READ-ONLY."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from xdesk.cmd_analytics import enrich_posts
from xdesk.lib.session import load_cookies, load_endpoints
from xdesk.lib.x_api import fetch_account_overview, fetch_content_page, summarize_overview
from xdesk.paths import OUT_DIR

ET = ZoneInfo("America/Indiana/Indianapolis")


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("digest", help="Weekly X analytics digest (read-only)")
    p.add_argument("--refresh-session", action="store_true")
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--out", type=str, default=None)
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
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
    for i, post in enumerate(c7, 1):
        text = (post.get("text") or "").replace("\n", " ")
        if len(text) > 100:
            text = text[:97] + "..."
        lines.append(f"  {i}. imp={post['impressions']} eng={post['engagements']} — {text}")
        if post.get("url"):
            lines.append(f"     {post['url']}")
    lines.append("")
    lines.append("Top posts (28d):")
    for i, post in enumerate(c28, 1):
        text = (post.get("text") or "").replace("\n", " ")
        if len(text) > 100:
            text = text[:97] + "..."
        lines.append(f"  {i}. imp={post['impressions']} eng={post['engagements']} — {text}")

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
