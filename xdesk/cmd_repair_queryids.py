"""xdesk repair-queryids — re-capture GraphQL queryIds via CDP. READ-ONLY."""
from __future__ import annotations

import argparse
import json

from xdesk.lib.cdp_capture import repair_queryids


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("repair-queryids", help="Re-capture X GraphQL queryIds via CDP")
    p.add_argument("--port", type=int, default=None, help="CDP port (default: 9228 preferred)")
    p.add_argument("--json-only", action="store_true")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
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
