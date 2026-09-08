"""
xdesk — trusted READ-ONLY X desk CLI.

GET-only. Never posts, likes, follows, DMs, or bookmarks.
No paid X API. Uses session cookies from chrome-profile-6 CDP.
"""
from __future__ import annotations

import argparse
import sys

from xdesk import __version__
from xdesk import (
    cmd_analytics,
    cmd_digest,
    cmd_refresh_session,
    cmd_repair_queryids,
    cmd_trends,
    cmd_why,
)
from xdesk.lib.cdp_capture import CdpError


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="xdesk",
        description="Trusted READ-ONLY X desk CLI for Dom (@spicefiendd). GET-only; no paid API.",
    )
    ap.add_argument("--version", action="version", version=f"xdesk {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    cmd_trends.add_parser(sub)
    cmd_why.add_parser(sub)
    cmd_analytics.add_parser(sub)
    cmd_digest.add_parser(sub)
    cmd_refresh_session.add_parser(sub)
    cmd_repair_queryids.add_parser(sub)
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    try:
        return int(args.func(args))
    except CdpError as e:
        print(f"CDP ERROR: {e}", file=sys.stderr)
        return 3
    except PermissionError as e:
        print(str(e), file=sys.stderr)
        return 2
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
