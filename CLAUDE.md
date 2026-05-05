# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Activate virtual environment (Windows)
venv\Scripts\activate.bat

# Install dependencies
pip install -r requirements.txt

# Run the FastAPI server (dev)
uvicorn main:app --reload

# Run the background orchestrator (separate process)
python orchestrator.py

# Database migrations
alembic revision --autogenerate -m "description of change"
alembic upgrade head
```

The `Procfile` starts only the web server. The orchestrator must be started as a **separate process** — it is never managed by the FastAPI app.

## Architecture

Two independent entry points:
- `main.py` — FastAPI app. Registers all routers and creates DB tables on startup via `models.Base.metadata.create_all()`.
- `orchestrator.py` — APScheduler `BlockingScheduler`. Manages all background jobs with `max_instances=1` to prevent overlapping runs.

### Request flow

Routes are thin HTTP contracts. All business logic lives in `services/`:

```
Route → Service → (Redis → SportsAPIClient fallback) + DB filtering
```

The only exception: `routes/teams.py` manages its own Redis cache directly (acceptable for its limited scope).

`routes/dev_tools.py` endpoints (e.g. DB sync) are **localhost-only** — they must not be exposed in production.

### Background job chain

The four nightly jobs run in a fixed dependency order — each one reads what the previous wrote into Redis:

```
00:30  prewarm_match_schedules()   →  matches:date:{YYYY-MM-DD}
00:45  prewarm_h2h_data()          →  h2h:teams:{id1}&{id2}        (reads Task 1's output)
01:00  prewarm_recent_matches()    →  team_recent_matches:{team_id} (reads Task 1's output)
01:15  calculate_live_windows()    →  LiveWorker.active_windows     (reads Task 1's output)
```

All times are `America/Mexico_City`. `calculate_live_windows()` also runs immediately on orchestrator startup to restore state after a restart.

`LiveWorker.active_windows` is **in-memory state on the instance** — it is lost if the orchestrator process restarts, which is why Task 4 re-runs on startup.

**Task 5** (`run_live_update`) runs every `WORKER_INTERVAL_MINUTES` (default 5) all day. It skips the API call if the current UTC time is outside every active window **and** `active_games_pending` is `False`.

### Redis

`get_redis_connection()` returns a `(client, error)` tuple — `client` may be `None`. Every caller must handle this gracefully; Redis is non-critical (the API returns `207 Degraded` when Redis is down but DB is healthy).

**Cache key ownership** (no two services write to the same pattern):

| Key pattern | TTL | Owner |
|---|---|---|
| `matches:date:{YYYY-MM-DD}` | 5 days | `MatchService` |
| `h2h:teams:{id1}&{id2}` | 10 days | `H2HService` |
| `team_recent_matches:{team_id}` | 24 h | `routes/teams.py` |
| `fixture_stats:{fixture_id}` | 30 days | `routes/teams.py` |
| `ml_recent_matches:{team_id}` | 24 h | `routes/ml.py` (fallback when prewarm cache is absent) |

Match data is **never written to the database** — it lives exclusively in Redis.

### Database

Only user-controlled persistent data lives in PostgreSQL: `leagues`, `countries`, `betting_tickets`.

- Routes inject sessions via `Depends(get_db)`.
- Background workers create `SessionLocal()` inside each job method and close it in a `finally` block — sessions are never stored on the class instance.

## Development rules

**Rate limiting** — place `time.sleep(0.6)` before every sequential, uncached call to API-Sports. Cached responses never trigger a sleep.

**Telegram notifications** — every background task sends exactly one success notification via `NotificationService.send_message()` on completion. No payload data; just a status line (e.g. `✅ Task Executed: H2H Data Cached`). Errors go to stdout only.

**Timezone convention** — user-facing date logic (determining "today") uses `America/Mexico_City`. Internal timestamps and window comparisons use UTC.
