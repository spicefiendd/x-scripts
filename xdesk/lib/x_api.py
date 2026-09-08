"""Read-only X internal GraphQL/REST helpers using session cookies."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from .session import auth_headers, load_endpoints

FEATURES_PATH = Path(__file__).resolve().parent / "features.json"
BASE = "https://x.com/i/api/graphql"


def _features() -> dict[str, Any]:
    return json.loads(FEATURES_PATH.read_text())


def _get(operation: str, variables: dict[str, Any], timeout: int = 45) -> dict[str, Any]:
    """GET graphql/{qid}/{operation}?variables=&features="""
    eps = load_endpoints()
    qid = eps["graphql"][operation]
    url = f"{BASE}/{qid}/{operation}"
    params = {
        "variables": json.dumps(variables, separators=(",", ":")),
        "features": json.dumps(_features(), separators=(",", ":")),
    }
    headers = auth_headers()
    r = requests.get(url, params=params, headers=headers, timeout=timeout)
    if r.status_code in (401, 403):
        raise PermissionError(
            f"X session rejected ({r.status_code}). Re-login as @spicefiendd in Chrome "
            f"(profile-6 / CDP 9228), then: xdesk refresh-session\nBody: {r.text[:200]}"
        )
    if r.status_code >= 400:
        raise RuntimeError(f"X API {r.status_code} for {operation}: {r.text[:300]}")
    return r.json()


def fetch_trending(count: int = 30) -> dict[str, Any]:
    eps = load_endpoints()
    variables = {
        "timelineId": eps["trending_timeline_id"],
        "count": count,
        "withQuickPromoteEligibilityTweetFields": True,
    }
    return _get("GenericTimelineById", variables)


def parse_trends(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract ranked trends + metadata from GenericTimelineById response."""
    trends: list[dict[str, Any]] = []

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            if o.get("name") and "trend_metadata" in o and "trend_url" in o:
                meta = o.get("trend_metadata") or {}
                url = o.get("trend_url") or {}
                trends.append(
                    {
                        "name": o.get("name"),
                        "rank": o.get("rank"),
                        "description": meta.get("meta_description")
                        or meta.get("domain_context")
                        or meta.get("snippet")
                        or o.get("description"),
                        "context": (o.get("social_context") or {}).get("text")
                        if isinstance(o.get("social_context"), dict)
                        else o.get("social_context"),
                        "url": url.get("url") if isinstance(url, dict) else url,
                        "is_ai_trend": o.get("is_ai_trend"),
                        "grouped": [
                            g.get("name")
                            for g in (o.get("grouped_trends") or [])
                            if isinstance(g, dict)
                        ],
                    }
                )
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(payload)
    by_name: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for t in trends:
        n = t.get("name")
        if not n:
            continue
        prev = by_name.get(n)
        if not prev:
            by_name[n] = t
            order.append(n)
            continue
        # Prefer entry with rank / grouped / description
        score = lambda x: (
            1 if x.get("rank") else 0,
            len(x.get("grouped") or []),
            1 if x.get("description") else 0,
        )
        if score(t) > score(prev):
            by_name[n] = t
    return [by_name[n] for n in order]


def _day_bounds_utc(days: int) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    end = now.replace(hour=23, minute=59, second=59, microsecond=999000)
    start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, end


def fetch_account_overview(days: int = 28) -> dict[str, Any]:
    start, end = _day_bounds_utc(days)
    prev_end = start
    prev_start = start - timedelta(days=days)
    backfill_from = end - timedelta(days=2)
    variables = {
        "current_from": int(start.timestamp() * 1000),
        "current_from_iso": start.strftime("%Y-%m-%dT00:00:00.000Z"),
        "current_to": int(end.timestamp() * 1000),
        "current_to_iso": end.strftime("%Y-%m-%dT00:00:00.000Z"),
        "prev_from": int(prev_start.timestamp() * 1000),
        "prev_from_iso": prev_start.strftime("%Y-%m-%dT00:00:00.000Z"),
        "prev_to": int(prev_end.timestamp() * 1000),
        "prev_to_iso": prev_end.strftime("%Y-%m-%dT00:00:00.000Z"),
        "backfill_from": int(backfill_from.timestamp() * 1000),
        "backfill_to": int(end.timestamp() * 1000),
        "show_verified_followers": True,
    }
    return _get("accountOverviewDailyQuery", variables)


def fetch_content_page(days: int = 7, max_results: int = 100) -> dict[str, Any]:
    start, end = _day_bounds_utc(days)
    variables = {
        "from_time": start.strftime("%Y-%m-%dT00:00:00.000Z"),
        "to_time": end.strftime("%Y-%m-%dT23:59:59.999Z"),
        "max_results": max_results,
        "query_page_size": 100,
        "requested_metrics": [
            "Impressions",
            "Likes",
            "Engagements",
            "Bookmark",
            "Share",
            "Follows",
            "Replies",
            "Retweets",
            "ProfileVisits",
            "DetailExpands",
            "UrlClicks",
            "HashtagClicks",
            "PermalinkClicks",
        ],
    }
    return _get("contentPageQuery", variables)


def summarize_overview(payload: dict[str, Any], days: int) -> dict[str, Any]:
    user = payload["data"]["viewer_v2"]["user_results"]["result"]
    cutoff = int((_day_bounds_utc(days)[0]).timestamp() * 1000)
    cur: dict[str, int] = defaultdict(int)
    for pt in user.get("current_time_series") or []:
        if pt.get("timestamp", 0) < cutoff:
            continue
        cur[pt.get("engagement_type", "?")] += int(pt.get("count") or 0)

    follows = unfollows = 0
    start_dt, _end_dt = _day_bounds_utc(days)
    for day in user.get("legacy_current_follow_metrics") or []:
        iso = ((day.get("timestamp") or {}).get("iso8601_time")) or ""
        if iso:
            try:
                day_dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
                if day_dt < start_dt:
                    continue
            except ValueError:
                pass
        for mv in day.get("metric_values") or []:
            if mv.get("metric_type") == "Follows":
                follows += int(mv.get("metric_value") or 0)
            elif mv.get("metric_type") == "Unfollows":
                unfollows += int(mv.get("metric_value") or 0)

    return {
        "followers": (user.get("relationship_counts") or {}).get("followers"),
        "verified_followers": user.get("verified_follower_count"),
        "days": days,
        "engagement_totals": dict(sorted(cur.items(), key=lambda kv: -kv[1])),
        "impressions_displayed": cur.get("Displayed", 0),
        "follows": follows,
        "unfollows": unfollows,
        "net_follows": follows - unfollows,
    }
