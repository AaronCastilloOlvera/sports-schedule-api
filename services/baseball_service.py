import json
from utils.redis_client import get_redis_connection
from services.mlb_api_client import MLBApiClient

SCHEDULE_TTL = 120   # 2 min — live scores change frequently
BOXSCORE_TTL = 120   # 2 min while live; completed games rarely re-fetched


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
