import json
from datetime import datetime
from utils.redis_client import get_redis_connection
from services.mlb_api_client import MLBApiClient

SCHEDULE_TTL = 120     # 2 min — live scores change frequently
BOXSCORE_TTL = 120     # 2 min while live; completed games rarely re-fetched
PITCHER_STATS_TTL = 21600  # 6h — season stats only move after a start ends
GAME_LOG_TTL = 21600       # 6h — same cadence, one new row appears every ~5 days


class BaseballService:
    def __init__(self):
        self.client = MLBApiClient()
        self.r, _ = get_redis_connection()

    def get_schedule(self, date: str, league: str = "lmb", force_refresh: bool = False) -> dict:
        cache_key = f"baseball:{league}:{date}"
        if not force_refresh and self.r:
            cached = self.r.get(cache_key)
            if cached:
                return {"data": json.loads(cached)}

        games = self.client.get_schedule(date, league)
        if self.r and games is not None:
            self.r.setex(cache_key, SCHEDULE_TTL, json.dumps(games))
        return {"data": games}

    def get_boxscore(self, game_pk: int) -> dict:
        cache_key = f"baseball:boxscore:{game_pk}"
        if self.r:
            cached = self.r.get(cache_key)
            if cached:
                return {"data": json.loads(cached)}

        box = self.client.get_boxscore(game_pk)
        if self.r and box:
            self.r.setex(cache_key, BOXSCORE_TTL, json.dumps(box))
        return {"data": box}

    def get_pitcher_stats(self, person_id: int, league: str = "lmb") -> dict:
        cache_key = f"baseball:pitcher-stats:{league}:{person_id}"
        if self.r:
            cached = self.r.get(cache_key)
            if cached:
                return {"data": json.loads(cached)}

        stats = self.client.get_person_stats(person_id, league)
        if self.r and stats:
            self.r.setex(cache_key, PITCHER_STATS_TTL, json.dumps(stats))
        return {"data": stats}

    def get_pitcher_game_log(self, person_id: int, league: str = "lmb", seasons: int = 5) -> dict:
        # seasons=5 → this year plus the 4 prior, e.g. 2022-2026. One cache
        # entry per (league, person, seasons) combo so a "just this year" caller
        # and a "5-year history" caller don't fight over the same key.
        current_year = datetime.now().year
        years = list(range(current_year, current_year - seasons, -1))

        cache_key = f"baseball:pitcher-gamelog:{league}:{person_id}:{seasons}"
        if self.r:
            cached = self.r.get(cache_key)
            if cached:
                return {"data": json.loads(cached)}

        log = self.client.get_person_game_log(person_id, league, years)
        if self.r and log:
            self.r.setex(cache_key, GAME_LOG_TTL, json.dumps(log))
        return {"data": log}
