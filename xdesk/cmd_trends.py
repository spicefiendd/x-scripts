"""xdesk trends — US Trending skim. READ-ONLY."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from xdesk.lib.session import load_cookies
from xdesk.lib.x_api import fetch_trending, parse_trends
from xdesk.paths import OUT_DIR


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("trends", help="Fetch US X trending (read-only)")
    p.add_argument("--count", type=int, default=25)
    p.add_argument("--refresh-session", action="store_true")
    p.add_argument("--json-only", action="store_true")
    p.add_argument("--out", type=str, default=None)
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    load_cookies(refresh=args.refresh_session)
    raw = fetch_trending(count=args.count)
    trends = parse_trends(raw)

    OUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = Path(args.out) if args.out else OUT_DIR / f"trending-{stamp}.json"
    payload = {
        "fetched_at_utc": stamp,
        "source": "GenericTimelineById (trending timeline)",
        "count": len(trends),
        "trends": trends,
        "raw": raw,
    }
    out_path.write_text(json.dumps(payload, indent=2))

    if args.json_only:
        print(json.dumps({"count": len(trends), "trends": trends, "out": str(out_path)}, indent=2))
    else:
        print(f"US Trending ({len(trends)})  — wrote {out_path}")
        for i, t in enumerate(trends[: args.count], 1):
            rank = t.get("rank") or i
            why = t.get("description") or t.get("context") or ""
            extra = f"  — {why}" if why else ""
            grouped = f" (also: {', '.join(t['grouped'][:3])})" if t.get("grouped") else ""
            print(f"{rank:>2}. {t.get('name')}{grouped}{extra}")
    return 0
