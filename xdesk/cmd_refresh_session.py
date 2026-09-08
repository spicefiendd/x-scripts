"""xdesk refresh-session — CDP cookie export. READ-ONLY."""
from __future__ import annotations

import argparse

from xdesk.lib.session import COOKIES_PATH, cookie_status, refresh_cookies_via_cdp


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("refresh-session", help="Refresh X session cookies via CDP")
    p.add_argument("--port", type=int, default=None, help="CDP port (default: try 9228,9224,9226)")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    cookies = refresh_cookies_via_cdp(port=args.port)
    if not cookies:
        print("FAILED: no cookies exported", file=__import__("sys").stderr)
        return 1
    print("OK wrote", COOKIES_PATH)
    print("status", cookie_status(cookies))
    return 0
