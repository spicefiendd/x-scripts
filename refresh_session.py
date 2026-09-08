#!/usr/bin/env python3
"""Refresh X auth cookies from the running Chrome profile via CDP. READ-ONLY."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.session import cookie_status, refresh_cookies_via_cdp, COOKIES_PATH  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Refresh @spicefiendd X session cookies via CDP")
    ap.add_argument("--port", type=int, default=None, help="CDP port (default: try 9228,9224,9226)")
    args = ap.parse_args()
    cookies = refresh_cookies_via_cdp(port=args.port)
    if not cookies:
        print("FAILED: no cookies exported", file=sys.stderr)
        return 1
    print("OK wrote", COOKIES_PATH)
    print("status", cookie_status(cookies))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
