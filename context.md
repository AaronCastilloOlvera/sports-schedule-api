# BetDash — Backend Context

> This document is the canonical reference for the BetDash FastAPI backend.
> Its primary purpose is to give any developer (or AI assistant) an accurate mental model of the architecture before touching the code.

---

## 1. Project Overview

BetDash is a **sports scheduling and statistics API** built to power a personal sports betting analytics app. The backend is responsible for:

- Serving real-time and historical match data (sourced from API-Sports).
- Caching all external API responses aggressively in Redis to stay within free-tier rate limits.
- Running nightly background jobs that pre-warm the Redis cache so every request is served from cache during peak hours.
- Tracking live match scores during active windows without wasting API calls during idle hours.
- Storing user-specific data (favorite leagues, betting tickets) in PostgreSQL.
- Analyzing betting ticket images using Google Gemini AI.

The app is deployed on Railway. The orchestrator runs as a separate process alongside the FastAPI server.

---

## 2. Tech Stack

| Layer | Technology |
|---|---|
| API Framework | FastAPI |
| Language | Python 3.11+ |
| Database ORM | SQLAlchemy |
| Database Migrations | Alembic |
| Database Engine | PostgreSQL (psycopg2 driver) |
| Cache | Redis |
| Background Scheduler | APScheduler (`BlockingScheduler`) |
| External Sports Data | API-Sports — `v3.football.api-sports.io` |
| AI / Image Analysis | Google Gemini (`google-genai`) |
| Image Processing | Pillow |
| Notifications | Telegram Bot API (via `NotificationService`) |
| HTTP Client | `requests` |
| Timezone Handling | `pytz`, `python-dateutil` |
| Server | Uvicorn |

---

## 3. Directory Architecture

```
sports-schedule-api/
│
├── main.py                  # FastAPI app factory — registers all routers, creates DB tables
├── orchestrator.py          # Single entry point for all background jobs (APScheduler)
├── alembic.ini              # Alembic migration configuration
├── requirements.txt         # Production dependencies
├── Procfile                 # Deployment process definitions
│
├── routes/                  # FastAPI routers — one file per resource domain
│   ├── matches.py           # /matches — by-date and head-to-head endpoints
│   ├── teams.py             # /teams — recent matches with stats
│   ├── leagues.py           # /leagues — CRUD and favorites management
│   ├── bets.py              # /bets — betting ticket storage and Gemini AI analysis
│   ├── redis.py             # /redis — cache inspection and manual refresh tools
│   ├── status.py            # /status — external API usage/quota monitoring
│   ├── dev_tools.py         # /dev-tools — localhost-only utilities (e.g. DB sync)
│   └── ml.py                # /ml — raw data export endpoints for the ML pipeline
│
├── services/                # Business logic layer — routes call services, not the DB or API directly
│   ├── sports_api_client.py # Thin wrapper around all API-Sports HTTP calls
│   ├── match_service.py     # Match fetching with Redis cache + DB favorite-league filtering
│   ├── H2HService.py        # Head-to-head fetching with Redis cache
│   ├── bet_service.py       # Betting ticket CRUD operations
│   └── notification_service.py  # Telegram message delivery
│
├── models/                  # SQLAlchemy ORM models (persisted data only)
│   ├── base.py              # declarative_base()
│   ├── league.py            # League (id, name, type, logo, is_favorite, country_id)
│   ├── country.py           # Country (id, name, code, flag)
│   └── betting_ticket.py    # BettingTicket (full ticket lifecycle with odds, stake, result)
│
├── tasks/                   # Background worker classes — one file per job domain
│   ├── prewarm_h2h.py       # PrewarmCacheWorker — match schedules + H2H cache jobs
│   ├── prewarm_recent_matches.py  # PrewarmRecentMatchesWorker — last 5 fixtures per team
│   └── live_matches.py      # LiveWorker — window calculation + live score polling
│
└── utils/                   # Shared infrastructure utilities
    ├── database.py          # SQLAlchemy engine, SessionLocal, get_db() dependency
    ├── redis_client.py      # get_redis_connection() — returns (client, error) tuple
    ├── schemas.py           # Pydantic request/response models
    └── constants.py         # API-Sports request headers
```

**Key rule:** Routes never call `SportsAPIClient` or the DB directly for business logic — they delegate to a service. The only exception is `routes/teams.py`, which is a thin route that manages its own Redis cache directly (acceptable for its limited scope).

---

## 4. Database & Caching Strategy

### 4.1 Database (PostgreSQL + SQLAlchemy)

The database stores only **user-controlled, persistent data**. Match data is never written to the database — it lives exclusively in Redis.

**Tables:**

| Model | Table | Purpose |
|---|---|---|
| `League` | `leagues` | All tracked leagues; `is_favorite` flag filters which matches are served |
| `Country` | `countries` | League metadata (name, code, flag) |
| `BettingTicket` | `betting_tickets` | Full history of user betting tickets with odds, stake, result, and analysis |

**Session management:** All routes use FastAPI's `Depends(get_db)` for automatic session injection and teardown. Background workers open and close their own sessions per job execution (not stored on the class instance) to avoid stale connections on long-running processes.

Migrations are managed with **Alembic** (`alembic upgrade head`).

### 4.2 Caching (Redis)

Redis is the **primary data store for all match data**. Every external API response is cached immediately after fetching. All routes check the cache first and only call the API on a miss.

**Cache key reference:**

| Key Pattern | TTL | Contents |
|---|---|---|
| `matches:date:{YYYY-MM-DD}` | 5 days | Filtered match list for a date (favorite leagues only) |
| `h2h:teams:{id1}&{id2}` | 10 days | All historical H2H fixtures between two teams |
| `team_recent_matches:{team_id}` | 24 hours | Last 5 fixtures for a team, enriched with per-fixture statistics |
| `fixture_stats:{fixture_id}` | 30 days | Full statistics block for a single fixture |
| `ml_recent_matches:{team_id}` | 24 hours | ML pipeline fallback cache (raw, non-enriched fixtures) |

**Cache layers for `team_recent_matches`:**
The `/teams/{team_id}/recent-matches` endpoint assembles its payload in two layers: it fetches the last 5 fixtures for the team, then for each fixture it checks `fixture_stats:{fixture_id}` before calling the API. Historical stats never change, so a 30-day TTL means each fixture's stats are only ever fetched once.

**Redis availability:** Redis is treated as non-critical. `get_redis_connection()` returns a `(client, error)` tuple — callers handle a `None` client gracefully. The `/health` endpoint reports `207 Degraded` if Redis is down but the database is healthy.

---

## 5. Orchestrator & Background Workers

The orchestrator is the **single entry point for all background jobs**, started independently from the FastAPI server (`python orchestrator.py`). It uses APScheduler's `BlockingScheduler` with `max_instances=1` on every job to prevent overlapping runs.

On startup, `calculate_live_windows()` is always called immediately so the live tracker is ready if the process is restarted mid-day.

### Nightly Pre-warm Sequence

The four nightly jobs run in a deliberate chronological chain. Each job depends on the output of the previous one being in Redis.

```
00:30  prewarm_match_schedules()   ─┐
00:45  prewarm_h2h_data()           ├─ PrewarmCacheWorker   (tasks/prewarm_h2h.py)
                                    │
01:00  prewarm_recent_matches()    ─── PrewarmRecentMatchesWorker (tasks/prewarm_recent_matches.py)
                                    │
01:15  calculate_live_windows()    ─── LiveWorker           (tasks/live_matches.py)
```

---

**TASK 1 — `prewarm_match_schedules()` at 00:30**
- Fetches match schedules for the next `DAYS_TO_PREWARM` days (default: 5) from the API-Sports `/fixtures` endpoint using `force_refresh=True`.
- Writes results to `matches:date:{YYYY-MM-DD}` in Redis.
- This is the data source for Tasks 2, 3, and 4 — none of them hit the API for match lists.
- Sends Telegram notification: `✅ Task Executed: Today's Matches Cached`

**TASK 2 — `prewarm_h2h_data()` at 00:45**
- Reads the freshly cached match schedules from Redis (no API call for match lists).
- For each fixture, calls `H2HService.get_headtohead_matches()` which writes to `h2h:teams:{id1}&{id2}`.
- Sends Telegram notification: `✅ Task Executed: H2H Data Cached`

**TASK 3 — `prewarm_recent_matches()` at 01:00**
- Reads today's cached match list to collect all unique team IDs.
- For each team, calls `SportsAPIClient.get_team_last_matches(team_id, last=5)`.
- For each of the 5 fixtures, checks `fixture_stats:{fixture_id}` — fetches from API only on a miss.
- Writes enriched payload (fixture + stats) to `team_recent_matches:{team_id}`.
- Sends Telegram notification: `✅ Task Executed: Recent Matches Cached`

**TASK 4 — `calculate_live_windows()` at 01:15**
- Reads today's cached match list and parses each fixture's kickoff time.
- Adds a 3-hour buffer per match to account for extra time and penalties.
- Merges overlapping time ranges into a minimal set of active windows.
- Stores the result in `LiveWorker.active_windows` (in-memory on the instance).
- Logs a daily API consumption estimate (total active minutes ÷ polling interval).

### Continuous Job

**TASK 5 — `run_live_update()` every `WORKER_INTERVAL_MINUTES` (default: 5 min)**
- Runs all day, every day.
- Checks if the current UTC time falls within any active window calculated by Task 4.
- Also runs if `active_games_pending` is `True` (a match is in-play but outside the scheduled window — handles overtime/delays).
- If active: calls `get_matches_by_date(force_refresh=True)` to update Redis with live scores.
- If idle: logs a sleep message and returns immediately (no API call made).

---

## 6. API Endpoints Reference

| Method | Path | Description |
|---|---|---|
| `GET` | `/matches/by-date?date=YYYY-MM-DD` | Fetch matches for a date (Redis → API fallback) |
| `GET` | `/matches/headtohead?team1=X&team2=Y` | H2H history between two teams |
| `GET` | `/teams/{team_id}/recent-matches` | Last 5 enriched fixtures for a team |
| `GET` | `/leagues` | All leagues |
| `GET` | `/leagues/favorite-leagues` | Leagues marked `is_favorite=true` |
| `PUT` | `/leagues/update-league` | Toggle `is_favorite` on a league |
| `GET` | `/bets/get-tickets` | Fetch all betting tickets |
| `POST` | `/bets/analyze-ticket` | Analyze a ticket with Gemini AI |
| `POST` | `/bets/upload-ticket-image` | Extract ticket data from an image via Gemini |
| `GET` | `/redis/keys` | Inspect all active Redis keys |
| `POST` | `/redis/refresh-fixtures-cache` | Manually force-refresh a date's match cache |
| `DELETE` | `/redis/flushdb` | Clear the entire Redis cache |
| `GET` | `/status/usage` | API-Sports quota and usage stats |
| `GET` | `/ml/export-h2h-json` | Raw data export for ML pipeline (last 5 matches per team + H2H) |
| `POST` | `/dev-tools/sync-db` | Sync DB (localhost only) |
| `GET` | `/health` | Health check — returns 200 / 207 (Redis down) / 503 (DB down) |

---

## 7. Development Standards

**Dependency Injection**
Database sessions are always injected via `Depends(get_db)` in routes. Background workers create their own `SessionLocal()` inside each job method and close it in a `finally` block. Sessions are never stored on class instances.

**Service Layer**
Routes are thin. All business logic (cache lookup → API fallback → DB filtering) lives in `services/`. A route file should read like a list of HTTP contracts, not a sequence of SQL and HTTP calls.

**API-Sports Rate Limiting**
A `time.sleep(0.6)` is placed before every sequential, uncached call to API-Sports to stay within the per-second rate limit. Cached responses never trigger a sleep.

**Telegram Notifications**
Every background task sends a single success notification via `NotificationService.send_message()` when it completes. No payload data is included — just a clean status line (e.g. `✅ Task Executed: H2H Data Cached`). Errors are logged to stdout only.

**Timezone Convention**
User-facing dates use `America/Mexico_City` local time (e.g. determining "today's" matches). Internal timestamps and window calculations use UTC. `pytz` handles all conversions.

**Cache Key Ownership**
Each service owns its cache keys. `H2HService` owns `h2h:teams:*`. `MatchService` owns `matches:date:*`. `routes/teams.py` owns `team_recent_matches:*` and `fixture_stats:*`. No two services write to the same key pattern.
