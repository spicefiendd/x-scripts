#!/usr/bin/env python3
"""
x_trending.py — US Trending skim for @spicefiendd X desk.
Replaces browserUse on https://x.com/explore/tabs/trending
READ-ONLY. Never posts.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.session import load_cookies  # noqa: E402
from lib.x_api import fetch_trending, parse_trends  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "out"


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch US X trending (read-only)")
    ap.add_argument("--count", type=int, default=25)
    ap.add_argument("--refresh-session", action="store_true")
    ap.add_argument("--json-only", action="store_true")
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()

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


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PermissionError as e:
        print(str(e), file=sys.stderr)
        raise SystemExit(2)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(1)
