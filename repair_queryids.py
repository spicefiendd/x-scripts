#!/usr/bin/env python3
"""
repair_queryids.py — CDP-navigate explore + search, rewrite lib/endpoints.json queryIds.

READ-ONLY network capture. Uses chrome-profile-6 CDP 9228 by default.
Also available as: x_trend_why.py --repair-queryids
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.cdp_capture import CdpError, repair_queryids  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Re-capture X GraphQL queryIds via CDP")
    ap.add_argument("--port", type=int, default=None, help="CDP port (default: 9228 preferred)")
    ap.add_argument("--json-only", action="store_true")
    args = ap.parse_args()
    result = repair_queryids(port=args.port)
    if args.json_only:
        print(json.dumps(result, indent=2))
    else:
        print(f"CDP port {result['port']}")
        print(f"found: {result['found']}")
        print(f"updated: {result['updated'] or '(none changed)'}")
        print(f"features_updated: {result['features_updated']}")
        print(f"wrote {result['endpoints_path']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CdpError as e:
        print(f"CDP ERROR: {e}", file=sys.stderr)
        raise SystemExit(3)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(1)
