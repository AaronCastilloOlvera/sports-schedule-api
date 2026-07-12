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
Route → Service → DB query (historical data)
Route → Service → Redis (live/today's schedule, odds)
```

`routes/dev_tools.py` endpoints are **localhost-only** — they must not be exposed in production. Current endpoints: `POST /dev-tools/sync-db`, `POST /dev-tools/persist-fixtures?date=YYYY-MM-DD`.

### Background job chain

All times are `America/Mexico_City`. Jobs that depend on previous output are grouped into pipelines and run sequentially via `await asyncio.to_thread()`.

```
00:15  run_nightly_pipeline()

         Phase 1 — Redis / prep
         ├── prewarm_match_schedules()   →  matches:date:{YYYY-MM-DD}
         ├── calculate_live_windows()    →  LiveWorker.active_windows     (reads step 1)
         └── prewarm_odds()              →  odds:{fixture_id}             (reads step 1)

         Phase 2 — DB persist
         ├── persist_recent_matches()    →  PostgreSQL últimos 5 por equipo (API → BD)
         ├── persist_h2h_fixtures()      →  PostgreSQL H2H history         (API → BD)
         └── persist_finished_fixtures() →  PostgreSQL finished fixtures   (API → BD)

∞      run_live_update()                (interval: WORKER_INTERVAL_MINUTES, default 5)
```

`calculate_live_windows()` also runs immediately on orchestrator startup to restore state after a restart.

`LiveWorker.active_windows` is **in-memory state on the instance** — it is lost if the orchestrator process restarts, which is why it re-runs on startup.

`run_live_update` skips the API call if the current UTC time is outside every active window **and** `active_games_pending` is `False`.

### Two data sources — why both exist

**Redis** holds ephemeral data that changes frequently or needs sub-millisecond access under high concurrency. Keys expire automatically; losing Redis degrades the API but never loses permanent data.

**PostgreSQL** holds all permanent data — both for ML training and for serving API endpoints. Historical fixture data (H2H, recent matches) is read directly from the DB by live endpoints.

| Data | Where | Why |
|---|---|---|
| Live match data | Redis only | Written every 5 min by `run_live_update`, read by every frontend poll simultaneously — high read/write frequency makes DB a bottleneck |
| Today's match schedules, odds | Redis only | Ephemeral — change frequently and don't need permanent storage |
| H2H history, recent match history | DB only | Persisted nightly; served by `/matches/headtohead` and `/teams/{id}/recent-matches` with full stats from `fixture_team_stats` |
| Finished fixture details (events, lineups, player stats) | DB only | Static after match ends; used for ML training and future analytics endpoints |
| League registry, betting tickets | DB only | User data — must be permanent |

### Redis

`get_redis_connection()` returns a `(client, error)` tuple — `client` may be `None`. Every caller must handle this gracefully; Redis is non-critical (the API returns `207 Degraded` when Redis is down but DB is healthy).

**Cache key ownership** (no two services write to the same pattern):

| Key pattern | TTL | Owner |
|---|---|---|
| `matches:date:{YYYY-MM-DD}` | 5 days | `MatchService` |
| `odds:{fixture_id}` | 12 h | `OddsService` |

Match data is **never written to the database** — it lives exclusively in Redis.

### Database

PostgreSQL holds all permanent data. Sessions are injected via `Depends(get_db)` in routes; background workers create `SessionLocal()` inside each job and close it in a `finally` block — sessions are never stored on the class instance.

#### Core tables
- `leagues` — favorite league registry
- `countries` — country reference data
- `betting_tickets` — user betting history

#### Fixture persistence tables (for ML, sports analytics, and API serving)

Populated nightly by the persist pipeline. The `fixtures.id` is the API-Sports fixture ID (no autoincrement) — makes every insert idempotent. H2H and recent-match endpoints read directly from these tables.

| Table | Rows per fixture | Purpose |
|---|---|---|
| `fixtures` | 1 | Match result, score, metadata |
| `fixture_team_stats` | 2 (one per team) | Shots, possession, xG, corners, cards — primary ML features |
| `fixture_events` | N | Goal/card/sub timeline — goal timing, momentum analysis |
| `fixture_lineups` | ~22 starters + subs | Formation, starting XI — detect key player absences |
| `fixture_player_stats` | ~22–26 | Individual ratings, goals, passes, tackles |

**Persist pipeline flow (Phase 2 — tres workers, todos API → BD directo):**

Cada worker checa `db.query(Fixture).filter(Fixture.id == fixture_id).first()` antes de hacer cualquier llamada de detalle — si el fixture ya está en BD, se salta por completo.

**Filtro de partidos juveniles** — los tres workers aplican `is_youth_match()` de `tasks/filters.py` antes de procesar. Solo filtra la liga Friendlies (ID 10) cuando algún equipo contiene patrón de categoría juvenil (U17, U21, Under-21, etc.). Otras ligas no se filtran.

| Worker | Llamadas de lista | Llamadas de detalle | Total por fixture |
|---|---|---|---|
| `persist_recent_matches` | 1 por equipo | 4 por fixture nuevo | 4 |
| `persist_h2h_fixtures` | 1 por par de equipos | 4 por fixture nuevo | 4 |
| `persist_finished_fixtures` | 0 (ya tiene la lista del schedule) | 4 por fixture nuevo | 4 |

**Costo aproximado asumiendo ~20 partidos/día y ~40 equipos únicos:**

*Noche 1 (BD vacía):*
- `persist_recent_matches`: 40 llamadas de lista + 40 equipos × 5 fixtures × 4 detalles = **840 calls**
- `persist_h2h_fixtures`: 20 llamadas de lista + 20 pares × 10 fixtures × 4 detalles = **820 calls**
- `persist_finished_fixtures`: 20 fixtures × 4 detalles = **80 calls**
- **Total noche 1: ~1,740 calls**

*Noches siguientes (BD ya poblada):*
- `persist_recent_matches`: 40 llamadas de lista + solo fixtures nuevos (~1 por equipo) × 4 = **200 calls**
- `persist_h2h_fixtures`: 20 llamadas de lista + solo el nuevo H2H por par × 4 = **100 calls**
- `persist_finished_fixtures`: 20 fixtures × 4 = **80 calls**
- **Total noche steady-state: ~380 calls**

El costo se autooptimiza — a medida que la BD se llena, las llamadas de detalle se eliminan. Solo se pagan listas (inevitables) + fixtures genuinamente nuevos. Con un límite de 7,500 calls/día, el pipeline nocturno ocupa ~5% del cupo en estado estable.

## Environment variables

| Variable | Default | Owner | Description |
|---|---|---|---|
| `DATABASE_URL` | — | DB | PostgreSQL connection string (required) |
| `REDIS_URL` | — | Redis | Full Redis URL; if absent, falls back to host/port/db below |
| `REDIS_HOST` | `localhost` | Redis | Redis host (used when `REDIS_URL` is not set) |
| `REDIS_PORT` | `6379` | Redis | Redis port |
| `REDIS_DB` | `0` | Redis | Redis logical DB index |
| `API_URL` | — | API-Sports | RapidAPI host for API-Sports (e.g. `v3.football.api-sports.io`) |
| `API_KEY` | — | API-Sports | RapidAPI key |
| `TELEGRAM_BOT_TOKEN` | — | Notifications | Telegram bot token for pipeline status messages |
| `TELEGRAM_CHAT_ID` | — | Notifications | Telegram chat/channel ID to receive messages |
| `GENAI_API_KEY` | — | Bets route | Google Gemini API key used by `/bets` AI analysis |
| `ALLOWED_ORIGINS` | `""` | CORS | Comma-separated list of allowed CORS origins |
| `APP_ENV` | — | Dev tools | Set to `localhost` to enable dev-tools endpoints |
| `PIPELINE_HOUR` | `0` | Orchestrator | Hour (MX time) when the nightly pipeline cron fires |
| `PIPELINE_MINUTE` | `15` | Orchestrator | Minute when the nightly pipeline cron fires |
| `WORKER_INTERVAL_MINUTES` | `5` | Orchestrator | How often `run_live_update` runs |
| `DAYS_TO_PREWARM` | `5` | `prewarm_h2h` | Days ahead to fetch and cache match schedules |
| `RECENT_MATCHES_DAYS` | `2` | `persist_recent_matches` | Days ahead to collect team IDs for last-5 recent matches |
| `H2H_DAYS` | `2` | `persist_h2h_fixtures` | Days ahead to collect team pairs for H2H history |
| `PG_DUMP_PATH` | — | Dev tools | Path to `pg_dump` binary (localhost DB sync only) |
| `PSQL_PATH` | — | Dev tools | Path to `psql` binary (localhost DB sync only) |
| `RAILWAY_DB_URL` | — | Dev tools | Source DB URL for localhost DB sync |

## Development rules

**Rate limiting** — place `time.sleep(0.6)` before every sequential, uncached call to API-Sports. Cached responses never trigger a sleep.

**Telegram notifications** — every background task sends exactly one success notification via `NotificationService.send_message()` on completion. No payload data; just a status line (e.g. `✅ Task Executed: H2H Data Cached`). Errors go to stdout only.

**Timezone convention** — user-facing date logic (determining "today") uses `America/Mexico_City`. Internal timestamps and window comparisons use UTC.

**Odds filtering** — `OddsService` only stores bet365, 1xBet, and Betano bookmakers. Defined in `ALLOWED_BOOKMAKERS` constant in `services/odds_service.py`.

**Alembic** — `migrations/env.py` imports `Base` from `models.base` and imports the `models` package so all models register for autogenerate detection. When adding a new model: create the file, import it in `models/__init__.py`, then run `alembic revision --autogenerate`.

**Memory — persist workers** — call `db.expunge_all()` after every `db.commit()` inside persist loops. SQLAlchemy's identity map holds references to every committed object until the session closes; without this, processing 100+ fixtures accumulates thousands of ORM objects in RAM.

## Future plans / Next features

### Feature: Historical fixture backfill worker

Worker on-demand que persiste fixtures históricos hacia atrás para alimentar el dataset de entrenamiento de `ml/predict_tomorrow.py`. El modelo necesita ~500-1,000 partidos para producir predicciones con señal real; sin este worker solo tiene los partidos que el pipeline nocturno ha acumulado desde que arrancó.

**Input:** una fecha (`YYYY-MM-DD`). El worker procesa todos los partidos terminados de ese día y los persiste en la BD. Para cubrir semanas o meses se llama múltiples veces, un día a la vez, yendo hacia atrás.

**Flujo por ejecución:**
1. Consultar presupuesto de API disponible (`SportsAPIClient.get_api_status()` → `requests_remaining`). Calcular cuántos fixtures puede procesar: `budget = (remaining - SAFETY_MARGIN) // CALLS_PER_FIXTURE`.
2. Llamar `GET /fixtures?date={date}` → lista de fixtures del día.
3. Filtrar los que ya existen en la tabla `fixtures` (idempotente — re-ejecutar el mismo día no hace nada).
4. Tomar los primeros `min(pendientes, budget)` fixtures.
5. Por cada fixture: fetch statistics + events + lineups + player_stats (3 llamadas, `time.sleep(0.6)` entre cada una). Persistir con la misma lógica de `PersistFinishedFixturesWorker._build_*`.
6. Retornar un resumen: `{date, persisted, skipped, api_calls_used, budget_remaining}`.

**Constantes clave:**
```python
CALLS_PER_FIXTURE = 3   # statistics + events + lineups (player_stats opcional)
SAFETY_MARGIN     = 500 # llamadas reservadas para el pipeline nocturno normal
```

**Cálculo de capacidad:** Con 7,500 llamadas/día y ~380 de consumo base del pipeline en estado estable, quedan ~7,100 disponibles. A 3 llamadas/fixture: ~1,800 fixtures/día máximo. Un día promedio tiene 15-30 partidos → el worker puede procesar ~60-180 días de historial por día de quota disponible. En la práctica, correr 1-2 veces al día procesando fechas distintas cada vez.

**Trigger:** endpoint dev-tools `POST /dev-tools/backfill?date=YYYY-MM-DD`. Localhost-only igual que los demás dev-tools. No hay cron — se llama manualmente cuando hay quota disponible.

**Progreso:** No guarda estado interno. La idempotencia viene de la BD (`fixtures.id` es el API-Sports fixture ID). Para recorrer semanas completas, llamar el endpoint con fechas consecutivas hacia atrás. Sugerencia: un script local que itere fechas y llame el endpoint en loop, parando si `budget_remaining < CALLS_PER_FIXTURE`.

**Advertencias:**
- `fixture_team_stats.expected_goals` puede ser `NULL` para partidos antiguos — el modelo lo maneja con impute.
- Algunas ligas menores no tienen statistics disponibles en API-Sports — persistir el fixture igual, los stats quedan vacíos.
- No incluir partidos con status distinto a `FT`, `AET`, `PEN` — misma constante `FINISHED_STATUSES` que el pipeline nocturno.

### Feature: Data retention worker

Un job que borra automáticamente fixtures y sus datos relacionados que superen N años de antigüedad, para mantener el tamaño de la BD acotado.

- `N` configurable vía variable de entorno (e.g. `FIXTURE_RETENTION_YEARS=10`).
- Borra en cascada: `fixture_player_stats` → `fixture_lineups` → `fixture_events` → `fixture_team_stats` → `fixtures`.
- Correr como cron mensual o trimestral — no necesita ser frecuente.
- Loggear cuántos fixtures se eliminaron y notificar por Telegram.

---

## Possible improvements

### Known (original)

| # | Area | Issue | Fix |
|---|---|---|---|
| 1 | **Logging** | `print()` everywhere — no log levels, no structured output | Replace with Python `logging` module; use `INFO`/`WARNING`/`ERROR` levels |
| 2 | **Persist pipeline** | Two workers using Redis as staging buffer for data only they consume | Merge into one worker: API → DB directly, skip Redis for events/lineups/player_stats |
| 3 | **Auth** | Zero authentication on any endpoint — dev-tools rely on `APP_ENV` env check | Add API key header check at minimum; use middleware so it's not per-route |
| 4 | **Error handling** | API-Sports failures are silently swallowed — no retry, no alerting | Add retry with exponential backoff on transient failures; Telegram alert on repeated failure |
| 5 | **Orchestrator resilience** | If orchestrator crashes, no automatic restart | Use Railway's restart policy or a process supervisor |
| 6 | **Migrations** | Alembic runs manually — easy to forget on deploy | Add `alembic upgrade head` as a release command in Railway |
| 7 | **Tests** | No test suite | At minimum: unit tests for `_build_*` methods in persist worker and service layer |

### Critical — Production outage risk

| # | File | Issue | Fix |
|---|---|---|---|
| C1 | `services/match_service.py` | Redis `None` not guarded before `.exists()` / `.setex()` — crashes when Redis is down instead of degrading | Check `if self.r` before every Redis call |
| C2 | `tasks/persist_*.py` (all 3) | Race condition between "check if fixture exists" read and insert — can produce `IntegrityError` under concurrent runs | Use DB-level upsert (`INSERT ON CONFLICT`) |
| C3 | `routes/bet_service.py` | File path traversal in image upload — `extension` taken from filename with no validation | Whitelist allowed extensions; reject filenames with path separators |
| C4 | `tasks/live_matches.py` | After Redis + orchestrator restart, `live_fixture_ids` restored from a potentially stale/empty Redis key | Re-calculate from API on startup instead of relying on persisted state |

### High — Data loss or corruption

| # | File | Issue | Fix |
|---|---|---|---|
| H1 | `services/match_service.py` | On force-refresh API failure, empty list overwrites valid cache data | Skip cache update on API failure; keep old data |
| H2 | `tasks/prewarm_finished_fixtures.py` | Enrichment data (stats, events, lineups) cached to Redis only — Redis loss drops it permanently | Fold into `PersistFinishedFixturesWorker` or write to DB directly |
| H3 | `services/sports_api_client.py` | All methods swallow API errors and return `[]` — full API outage looks identical to "no matches today" | Raise exceptions; add Telegram alert after N failures |
| H4 | `orchestrator.py` | No timeout on jobs — if `run_nightly_pipeline()` hangs, next day's job is permanently blocked | Add `timeout=3600` and handle `JobExecutionError` |
| H5 | `main.py` | `create_all()` on startup never checks if Alembic version matches; schema drift goes undetected | Add startup check comparing alembic version to expected |

### Medium — Degraded service or reliability

| # | File | Issue | Fix |
|---|---|---|---|
| M1 | `services/H2HService.py` | N+1 query problem — one `SELECT` per fixture instead of a join | Use `joinedload(Fixture.team_stats)` |
| M2 | `routes/redis.py` | `r.keys("*")` blocks Redis on large datasets | Replace with `r.scan()` pagination |
| M3 | `tasks/live_matches.py`, `tasks/prewarm_*.py` | TTL constants redefined in multiple files with different values — keys silently overwrite each other's TTL | Centralize all TTL constants in `utils/constants.py` |
| M4 | `orchestrator.py` | `PrewarmRecentMatchesWorker` and `PrewarmFinishedFixturesWorker` imported but never scheduled | Decide if they should run nightly; if so, add to pipeline |
| M5 | `orchestrator.py` | No broad exception handler — non-keyboard exceptions crash the scheduler silently | Add `except Exception` handler; log and exit cleanly |
| M6 | `utils/database.py` | SQLAlchemy engine uses default pool settings — no size limit or timeout configured | Add `pool_size`, `max_overflow`, `pool_timeout` |
| M7 | `routes/bets.py` | `genai.Client` initialized at module level — missing env var crashes app on import | Lazy-initialize on first request with clear error message |

### Low — Code quality and maintainability

| # | File | Issue | Fix |
|---|---|---|---|
| L1 | `routes/leagues.py:72–74` | Orphaned `from datetime import datetime` + `...` from incomplete refactoring | Remove dead lines |
| L2 | `tasks/persist_*.py` (all 3) | ~80% code duplication across workers | Extract shared `_persist_fixture()` utility into base class |
| L3 | `tasks/live_matches.py`, `tasks/persist_*.py` | Magic numbers (`timedelta(hours=3)`, `last=5`, etc.) with no named constants | Move to named constants with explanatory comments |
| L4 | `tasks/persist_*.py` | Individual `db.add()` + `db.commit()` per fixture instead of bulk insert | Use `bulk_insert_mappings()` for team_stats, events, lineups, player_stats |
| L5 | `services/notification_service.py` | `send_message()` returns nothing — callers cannot know if notification failed | Return a boolean so callers can retry or escalate |
| L6 | All tasks | Emoji in `print()` calls are not machine-parseable; blocks structured log monitoring | Use `logging` module with `INFO`/`WARNING`/`ERROR` levels |
| L7 | All routes | Most endpoints have no `response_model` — Swagger shows `dict` instead of a schema | Add `response_model` to all route decorators |
| L8 | `services/odds_service.py` | `ALLOWED_BOOKMAKERS` is hardcoded — adding a bookmaker requires a code change | Move to env var or DB config table |
| L9 | `services/sports_api_client.py` | Never reads `X-RateLimit-*` response headers — quota exhaustion is a surprise | Parse headers; warn when approaching limit |

---

## ML feature notes

The fixture persistence tables are designed for training match outcome prediction models.

- **Core features** live in `fixture_team_stats` joined with `fixtures` — start here.
- **Form features** (last N matches per team) are a rolling window query over `fixtures` ordered by `date_utc`.
- **xG** (`expected_goals`) is the highest-signal single feature but is nullable — handle its absence gracefully.
- **Lineup features** (key player absent, formation change) require `fixture_lineups`.
- **Goal timing** (scoring first, comeback wins) requires `fixture_events`.
- Player-level features add noise in small datasets — weight `fixture_player_stats` only once you have 1,000+ matches persisted.
