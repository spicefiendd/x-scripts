#!/usr/bin/env python3
"""
Optional: quick live search skim via SearchTimeline GraphQL.
READ-ONLY.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.session import load_cookies  # noqa: E402
from lib.x_api import _get  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "out"


def fetch_search(query: str, count: int = 20, product: str = "Latest") -> dict:
    variables = {
        "rawQuery": query,
        "count": count,
        "querySource": "typed_query",
        "product": product,  # Latest | Top
        "withGrokTranslatedBio": False,
        "withQuickPromoteEligibilityTweetFields": False,
    }
    return _get("SearchTimeline", variables)


def parse_hits(payload: dict, limit: int = 10) -> list[dict]:
    hits: list[dict] = []

    def walk(o):
        if isinstance(o, dict):
            # Tweet result
            if o.get("__typename") == "Tweet" and "legacy" in o:
                leg = o["legacy"]
                user = (((o.get("core") or {}).get("user_results") or {}).get("result") or {})
                core = user.get("core") or {}
                screen = core.get("screen_name")
                tid = o.get("rest_id") or leg.get("id_str")
                hits.append(
                    {
                        "id": tid,
                        "text": leg.get("full_text"),
                        "likes": leg.get("favorite_count"),
                        "retweets": leg.get("retweet_count"),
                        "user": screen,
                        "url": f"https://x.com/{screen}/status/{tid}" if screen and tid else None,
                    }
                )
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(payload)
    # de-dupe
    seen = set()
    out = []
    for h in hits:
        if h["id"] in seen:
            continue
        seen.add(h["id"])
        out.append(h)
    out.sort(key=lambda x: (x.get("likes") or 0), reverse=True)
    return out[:limit]


def main() -> int:
    ap = argparse.ArgumentParser(description="Quick X search skim (read-only)")
    ap.add_argument("query")
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--product", choices=["Latest", "Top"], default="Latest")
    ap.add_argument("--refresh-session", action="store_true")
    args = ap.parse_args()
    load_cookies(refresh=args.refresh_session)
    raw = fetch_search(args.query, count=args.count, product=args.product)
    hits = parse_hits(raw, limit=min(args.count, 15))
    OUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe = quote(args.query, safe="")[:40]
    out = OUT_DIR / f"search-{safe}-{stamp}.json"
    out.write_text(json.dumps({"query": args.query, "hits": hits, "raw": raw}, indent=2))
    print(f"Search {args.query!r} ({args.product}) — {len(hits)} hits — {out}")
    for i, h in enumerate(hits, 1):
        text = (h.get("text") or "").replace("\n", " ")[:100]
        print(f"{i}. @{h.get('user')} ❤{h.get('likes')} — {text}")
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
