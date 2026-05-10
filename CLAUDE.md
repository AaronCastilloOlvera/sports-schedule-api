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
- `orchestrator.py` — APScheduler `AsyncIOScheduler`. Manages all background jobs with `max_instances=1` to prevent overlapping runs.

### Request flow

Routes are thin HTTP contracts. All business logic lives in `services/`:

```
Route → Service → (Redis → SportsAPIClient fallback) + DB filtering
```

The only exception: `routes/teams.py` manages its own Redis cache directly (acceptable for its limited scope).

`routes/dev_tools.py` endpoints (e.g. DB sync) are **localhost-only** — they must not be exposed in production.

### Background job chain

All times are `America/Mexico_City`. Jobs that depend on previous output are grouped into pipelines and run sequentially via `await asyncio.to_thread()`.

```
00:15  run_nightly_pipeline()
         ├── prewarm_match_schedules()   →  matches:date:{YYYY-MM-DD}
         ├── prewarm_h2h_data()          →  h2h:teams:{id1}&{id2}        (reads pipeline step 1)
         ├── prewarm_recent_matches()    →  team_recent_matches:{team_id} (reads pipeline step 1)
         └── prewarm_odds()              →  odds:{fixture_id}             (reads pipeline step 1)

01:15  calculate_live_windows()         →  LiveWorker.active_windows     (reads nightly pipeline output)

02:00  run_persist_pipeline()
         ├── prewarm_finished_fixtures() →  fixture_events:{fixture_id}
         │                                  fixture_lineups:{fixture_id}
         │                                  fixture_player_stats:{fixture_id}
         └── persist_finished_fixtures() →  PostgreSQL (reads all Redis keys above)

∞      run_live_update()                (interval: WORKER_INTERVAL_MINUTES, default 5)
```

`calculate_live_windows()` also runs immediately on orchestrator startup to restore state after a restart.

`LiveWorker.active_windows` is **in-memory state on the instance** — it is lost if the orchestrator process restarts, which is why it re-runs on startup.

`run_live_update` skips the API call if the current UTC time is outside every active window **and** `active_games_pending` is `False`.

### Redis

`get_redis_connection()` returns a `(client, error)` tuple — `client` may be `None`. Every caller must handle this gracefully; Redis is non-critical (the API returns `207 Degraded` when Redis is down but DB is healthy).

**Cache key ownership** (no two services write to the same pattern):

| Key pattern | TTL | Owner |
|---|---|---|
| `matches:date:{YYYY-MM-DD}` | 5 days | `MatchService` |
| `h2h:teams:{id1}&{id2}` | 10 days | `H2HService` |
| `team_recent_matches:{team_id}` | 24 h | `routes/teams.py` |
| `fixture_stats:{fixture_id}` | 30 days | `routes/teams.py` + `PrewarmFinishedFixturesWorker` (fallback) |
| `fixture_events:{fixture_id}` | 48 h | `PrewarmFinishedFixturesWorker` |
| `fixture_lineups:{fixture_id}` | 48 h | `PrewarmFinishedFixturesWorker` |
| `fixture_player_stats:{fixture_id}` | 48 h | `PrewarmFinishedFixturesWorker` |
| `ml_recent_matches:{team_id}` | 24 h | `routes/ml.py` (fallback when prewarm cache is absent) |
| `odds:{fixture_id}` | 12 h | `OddsService` |

Match data is **never written to the database** — it lives exclusively in Redis.

### Database

PostgreSQL holds all permanent data. Sessions are injected via `Depends(get_db)` in routes; background workers create `SessionLocal()` inside each job and close it in a `finally` block — sessions are never stored on the class instance.

#### Core tables
- `leagues` — favorite league registry
- `countries` — country reference data
- `betting_tickets` — user betting history

#### Fixture persistence tables (for ML & sports analytics)

Populated nightly by `run_persist_pipeline()` after matches finish. The `fixtures.id` is the API-Sports fixture ID (no autoincrement) — makes every insert idempotent.

| Table | Rows per fixture | Purpose |
|---|---|---|
| `fixtures` | 1 | Match result, score, metadata |
| `fixture_team_stats` | 2 (one per team) | Shots, possession, xG, corners, cards — primary ML features |
| `fixture_events` | N | Goal/card/sub timeline — goal timing, momentum analysis |
| `fixture_lineups` | ~22 starters + subs | Formation, starting XI — detect key player absences |
| `fixture_player_stats` | ~22–26 | Individual ratings, goals, passes, tackles |

**Persist pipeline flow:**
1. `PrewarmFinishedFixturesWorker` — reads finished fixtures from Redis, fetches missing data from API-Sports, writes to Redis (48 h TTL). `fixture_stats` is skipped if already cached by `routes/teams.py`.
2. `PersistFinishedFixturesWorker` — reads all 4 Redis keys per fixture, writes to PostgreSQL. Zero API calls. Skips fixtures already in DB.

**API cost:** ~60-80 extra calls/day with ~20 finished fixtures. Well within the 7,500/day plan limit.

## Development rules

**Rate limiting** — place `time.sleep(0.6)` before every sequential, uncached call to API-Sports. Cached responses never trigger a sleep.

**Telegram notifications** — every background task sends exactly one success notification via `NotificationService.send_message()` on completion. No payload data; just a status line (e.g. `✅ Task Executed: H2H Data Cached`). Errors go to stdout only.

**Timezone convention** — user-facing date logic (determining "today") uses `America/Mexico_City`. Internal timestamps and window comparisons use UTC.

**Odds filtering** — `OddsService` only stores bet365, 1xBet, and Betano bookmakers. Defined in `ALLOWED_BOOKMAKERS` constant in `services/odds_service.py`.

**Alembic** — `migrations/env.py` imports `Base` from `models.base` and imports the `models` package so all models register for autogenerate detection. When adding a new model: create the file, import it in `models/__init__.py`, then run `alembic revision --autogenerate`.

## ML feature notes

The fixture persistence tables are designed for training match outcome prediction models.

- **Core features** live in `fixture_team_stats` joined with `fixtures` — start here.
- **Form features** (last N matches per team) are a rolling window query over `fixtures` ordered by `date_utc`.
- **xG** (`expected_goals`) is the highest-signal single feature but is nullable — handle its absence gracefully.
- **Lineup features** (key player absent, formation change) require `fixture_lineups`.
- **Goal timing** (scoring first, comeback wins) requires `fixture_events`.
- Player-level features add noise in small datasets — weight `fixture_player_stats` only once you have 1,000+ matches persisted.
