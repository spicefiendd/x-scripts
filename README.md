# x-scripts — reusable X desk helpers for @spicefiendd

Read-only Shell scripts that replace expensive **browserUse** skims on the shared box.
They call X’s internal web GraphQL endpoints with cookies from the signed-in Chrome
profile — same idea as `/workspace/yahoo-mail-scripts/` (reuse the live browser
session; never type passwords).

**Never posts, likes, replies, follows, or DMs.**

## What they replace

| Old browserUse workflow | Script |
|---|---|
| https://x.com/explore/tabs/trending skim | `x_trending.py` |
| Why is this trending? (Top posts + hooks) | `x_trend_why.py` / `x-trend-why` |
| https://x.com/i/account_analytics (+ content / 7d) | `x_analytics.py` |
| Sunday weekly analytics digest | `x_digest.py` |
| Optional topic live skim | `x_search.py` (often 404; prefer `x_trend_why`) |

## Setup

```bash
cd /workspace/x-scripts
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### Session (cookies)

Chrome on this box is launched with `--password-store=basic` and cookies are
encrypted on disk, so we **export decrypted cookies via CDP** from the already
running X-desk profile (do not print full secrets):

| Profile | CDP port | Notes |
|---|---|---|
| `/home/box/chrome-profile-6` | **9228** | Preferred — trending / search / spicefiendd |
| `/home/box/chrome-profile-2` | 9224 | Also has analytics history |

```bash
# Chrome must already be signed in as @spicefiendd
.venv/bin/python refresh_session.py
# or pin a port:
.venv/bin/python refresh_session.py --port 9228
```

Writes mode-600 `.session_cookies.json` (`auth_token`, `ct0`, …).  
Public web bearer lives in `.bearer.txt` (not a user secret).

Env overrides: `X_CDP_PORT`, `X_SESSION_COOKIES`, `X_BEARER_FILE`.

## Run commands

```bash
cd /workspace/x-scripts

# US trending (text + JSON under out/)
.venv/bin/python x_trending.py
.venv/bin/python x_trending.py --count 20 --refresh-session

# Account analytics overview (28d) + top posts (7d)
.venv/bin/python x_analytics.py
.venv/bin/python x_analytics.py --days 7 --overview-days 28 --top 10
.venv/bin/python x_analytics.py --also-28d-content --json-only

# Sunday digest Dom expects
.venv/bin/python x_digest.py --top 5

# Why is a topic trending? (CDP SearchTimeline capture — preferred)
.venv/bin/python x_trend_why.py "Michigan" --count 5
.venv/bin/python x-trend-why "Clancy mistrial" --count 8 --product Top
.venv/bin/python x_trend_why.py "AI" --refresh-session --json-only
# --refresh-session exports cookies from chrome-profile-6 CDP **9228**

# Re-capture GraphQL queryIds when X rotates them
.venv/bin/python repair_queryids.py
.venv/bin/python x_trend_why.py --repair-queryids

# Optional search skim (plain requests; often 404 without live transaction id)
.venv/bin/python x_search.py "Clancy mistrial" --count 20
```

Outputs land in `out/` e.g.:

- `out/trending-YYYYMMDDThhmmssZ.json`
- `out/trend-why-<slug>-YYYYMMDDThhmmssZ.json`
- `out/analytics-YYYYMMDDThhmmssZ.json`
- `out/digest-YYYYMMDDThhmmssZ.txt` (+ `.json`)

## Endpoints used

All `GET https://x.com/i/api/graphql/{queryId}/{Operation}` with session cookies +
public web `Authorization: Bearer …` + `x-csrf-token: <ct0>`.

| Operation | queryId (see `lib/endpoints.json`) | Purpose |
|---|---|---|
| `GenericTimelineById` | `ee4dBLWL8a8qg6n19m1htQ` | US trending list (`timelineId` = trending) |
| `accountOverviewDailyQuery` | `_P1caq0YB4SVuEtFLPDMfQ` | 7d/28d overview time series, follows |
| `contentPageQuery` | `eyqFN-MJHrF7Aq4O5aFBpQ` | Top posts + organic metrics |
| `ExplorePage` | `jo4rJIWiO5pQlMk6FYphZQ` | Explore hub (captured; trending uses GenericTimeline) |

Feature flags: `lib/features.json` (copied from live Chrome 151 client).  
Account IDs live in gitignored `lib/account.local.json` (see `account.local.json.example`) — never commit them.

`x_search.py` uses `SearchTimeline` GraphQL (`hyPfJYJ_XAtDYoslQc-Rgg`) via plain requests. It often requires a live `x-client-transaction-id` and may 404 — treat as experimental.

**`x_trend_why.py`** is the supported “why trending” path: it CDP-navigates
`https://x.com/search?q=…&f=top` on **chrome-profile-6 port 9228**, captures the
live `SearchTimeline` Network response (same pattern as `_dev/_cdp_search3.py`),
parses hooks (author, text, likes, RTs, views), optionally attaches US trend
metadata from `GenericTimelineById`, and writes `out/trend-why-*.json`. Falls
back to requests if CDP is down. Tab hygiene: reuses an existing x.com tab;
closes any tab it created.

If GraphQL queryIds rotate: `.venv/bin/python repair_queryids.py` (or
`x_trend_why.py --repair-queryids`).

## Failure modes

| Symptom | Fix |
|---|---|
| `PermissionError` / 401 / 403 | Session expired → open X in chrome-profile-6, log in as spicefiendd, `refresh_session.py` |
| CDP handshake / connection refused | Chrome not running on that port — start the box Chrome profile with remote debugging |
| Empty / wrong trends | queryId or `trending_timeline_id` rotated — `repair_queryids.py` or `_cdp_capture_trending.py` |
| `x_trend_why` CDP timeout / no SearchTimeline | Chrome not signed in, or no x.com tab — open X on profile-6, then retry |
| `x_search.py` 404 | Expected without live transaction id — use `x_trend_why.py` instead |
| Analytics shape errors | GraphQL field rename — re-capture account analytics navigation |
| Cookie decrypt from SQLite fails | Expected on this box; always use CDP refresh, not raw Cookies DB |

**Fragile bits:** GraphQL `queryId`s and the features blob change with client deploys.
Cookies die when X invalidates the session. `x-client-transaction-id` is *not*
required today for these GETs but may be later.

## Safety

- Read-only by design (GET only).
- Do not commit `.session_cookies.json` / `.bearer.txt` (see `.gitignore`).
- Do not log full `auth_token` / `ct0`.
- Do not ask Dom for passwords; re-login only via the existing browser profile.

## Layout

```
x-scripts/
  refresh_session.py
  x_trending.py
  x_trend_why.py       # why-trending (CDP SearchTimeline) + symlink x-trend-why
  repair_queryids.py   # re-capture GraphQL queryIds via CDP
  x_analytics.py
  x_digest.py
  x_search.py
  lib/session.py       # CDP cookie export + auth headers
  lib/x_api.py         # GraphQL client + parsers
  lib/cdp_capture.py   # CDP navigate + Network capture (tab hygiene)
  lib/endpoints.json
  lib/features.json
  out/                 # JSON/text artifacts
  requirements.txt
```
