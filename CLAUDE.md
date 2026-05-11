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

`routes/dev_tools.py` endpoints are **localhost-only** — they must not be exposed in production. Current endpoints: `POST /dev-tools/sync-db`, `POST /dev-tools/persist-fixtures?date=YYYY-MM-DD`.

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

### Two data sources — why both exist

**Redis** holds ephemeral, frequently-read data that serves live API traffic. Keys expire automatically; losing Redis degrades the API but never loses permanent data. All keys here are read by HTTP endpoints to avoid hitting API-Sports on every request.

**PostgreSQL** holds permanent data that never expires. Fixture detail tables exist exclusively for ML training — they are never read by live API endpoints (no endpoint queries `fixture_events` or `fixture_lineups` from the DB today; future endpoints will).

| Data | Where | Why |
|---|---|---|
| Live match data | Redis only | Written every 5 min by `run_live_update`, read by every frontend poll simultaneously — high read/write frequency makes DB a bottleneck |
| Today's match schedules, H2H, recent matches, odds | Redis only | Cached to avoid repeated API-Sports calls, not for DB performance — these could live in PostgreSQL but that would burn API quota on every request |
| Finished fixture details (events, lineups, player stats, team stats) | **DB only** (via nightly pipeline) | Static after the match ends; only needed for ML training |
| League registry, betting tickets | DB only | User data — must be permanent |

### Redis

`get_redis_connection()` returns a `(client, error)` tuple — `client` may be `None`. Every caller must handle this gracefully; Redis is non-critical (the API returns `207 Degraded` when Redis is down but DB is healthy).

**Cache key ownership** (no two services write to the same pattern):

| Key pattern | TTL | Owner |
|---|---|---|
| `matches:date:{YYYY-MM-DD}` | 5 days | `MatchService` |
| `h2h:teams:{id1}&{id2}` | 10 days | `H2HService` |
| `team_recent_matches:{team_id}` | 24 h | `routes/teams.py` |
| `fixture_stats:{fixture_id}` | 30 days | `routes/teams.py` (serves recent-matches endpoint) |
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

## Future plans / Next features

### Feature: Historical fixture backfill worker

Un worker que recorre temporadas anteriores hacia atrás y persiste fixtures históricos para enriquecer el dataset de entrenamiento del modelo ML.

**Comportamiento clave — API-aware:**
Antes de cada ejecución, el worker debe consultar el uso actual de la API (`GET /status` en API-Sports devuelve llamadas usadas/disponibles del día) y calcular cuántos fixtures puede procesar sin exceder el límite diario. Con 7,500 llamadas/día y ~1,500 de consumo base, hay ~6,000 disponibles. A 3 llamadas por fixture (events + lineups + player_stats), puede procesar ~2,000 fixtures por día si no hay otros jobs corriendo — en la práctica dejar un margen y procesar ~50-100 fixtures por ejecución.

**Diseño sugerido:**
- Nuevo endpoint `GET /status/usage` ya existe — usarlo para calcular el presupuesto disponible antes de iniciar.
- El worker recibe una `league_id` y una `season` como parámetros.
- Llama a `GET /fixtures?league={id}&season={year}` para obtener todos los fixture IDs de esa temporada.
- Filtra los que ya existen en `fixtures` (idempotente).
- Procesa hasta N fixtures según el presupuesto de API disponible, guarda el progreso (último fixture procesado) para reanudar en la siguiente ejecución.
- Misma lógica que `prewarm_finished_fixtures` + `persist_finished_fixtures` pero sin depender de Redis de matches del día.
- Correr manualmente vía endpoint dev-tools, no como cron automático.

**Advertencia:** statistics (`/fixtures/statistics`) puede no estar disponible para partidos muy antiguos dependiendo del plan y la liga.

### Feature: Data retention worker

Un job que borra automáticamente fixtures y sus datos relacionados que superen N años de antigüedad, para mantener el tamaño de la BD acotado.

- `N` configurable vía variable de entorno (e.g. `FIXTURE_RETENTION_YEARS=10`).
- Borra en cascada: `fixture_player_stats` → `fixture_lineups` → `fixture_events` → `fixture_team_stats` → `fixtures`.
- Correr como cron mensual o trimestral — no necesita ser frecuente.
- Loggear cuántos fixtures se eliminaron y notificar por Telegram.

---

## Possible improvements

| # | Area | Issue | Fix |
|---|---|---|---|
| 1 | **Logging** | `print()` everywhere — no log levels, no structured output | Replace with Python `logging` module; use `INFO`/`WARNING`/`ERROR` levels |
| 2 | **Persist pipeline** | Two workers using Redis as staging buffer for data only they consume | Merge into one worker: API → DB directly, skip Redis for events/lineups/player_stats |
| 3 | **Auth** | Zero authentication on any endpoint — dev-tools rely on `APP_ENV` env check | Add API key header check at minimum; use middleware so it's not per-route |
| 4 | **Error handling** | API-Sports failures are silently swallowed — no retry, no alerting | Add retry with exponential backoff on transient failures; Telegram alert on repeated failure |
| 5 | **Orchestrator resilience** | If orchestrator crashes, no automatic restart | Use Railway's restart policy or a process supervisor |
| 6 | **Migrations** | Alembic runs manually — easy to forget on deploy | Add `alembic upgrade head` as a release command in Railway |
| 7 | **Tests** | No test suite | At minimum: unit tests for `_build_*` methods in persist worker and service layer |

---

## ML feature notes

The fixture persistence tables are designed for training match outcome prediction models.

- **Core features** live in `fixture_team_stats` joined with `fixtures` — start here.
- **Form features** (last N matches per team) are a rolling window query over `fixtures` ordered by `date_utc`.
- **xG** (`expected_goals`) is the highest-signal single feature but is nullable — handle its absence gracefully.
- **Lineup features** (key player absent, formation change) require `fixture_lineups`.
- **Goal timing** (scoring first, comeback wins) requires `fixture_events`.
- Player-level features add noise in small datasets — weight `fixture_player_stats` only once you have 1,000+ matches persisted.
