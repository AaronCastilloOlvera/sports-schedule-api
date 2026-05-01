import json
import time
from fastapi import APIRouter, HTTPException
from utils.redis_client import get_redis_connection
from services.sports_api_client import SportsAPIClient

router = APIRouter(prefix="/teams", tags=["teams"])

RECENT_MATCHES_TTL = 86400  # 24 hours  — team_recent_matches:{team_id}
STATS_TTL = 2592000  # 30 days   — fixture_stats:{fixture_id}

@router.get("/{team_id}/recent-matches")
def get_team_recent_matches(team_id: int):
    r, redis_error = get_redis_connection()
    if r is None:
        raise HTTPException(status_code=500, detail=f"Redis unavailable: {redis_error}")

    # ── Cache hit ──────────────────────────────────────────────────────────────
    cached = r.get(f"team_recent_matches:{team_id}")
    if cached:
        return {"team_id": team_id, "source": "cache", "data": json.loads(cached)}

    # ── Cache miss: assemble from API ──────────────────────────────────────────
    api = SportsAPIClient()

    fixtures = api.get_team_recent_form(team_id, last=5)
    if not fixtures:
        raise HTTPException(status_code=404, detail=f"No recent fixtures found for team {team_id}.")

    enriched = []
    for fixture in fixtures:
        fixture_id = fixture["fixture"]["id"]
        stats_key  = f"fixture_stats:{fixture_id}"

        raw = r.get(stats_key)
        if raw:
            stats = json.loads(raw)
        else:
            time.sleep(0.6)  # respect API-Sports per-second rate limit
            stats = api.get_fixture_statistics(fixture_id)
            if stats:
                r.setex(stats_key, STATS_TTL, json.dumps(stats))

        enriched.append({**fixture, "statistics": stats})

    r.setex(f"team_recent_matches:{team_id}", RECENT_MATCHES_TTL, json.dumps(enriched))

    return {"team_id": team_id, "source": "assembled", "data": enriched}
