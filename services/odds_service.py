import json
from utils.redis_client import get_redis_connection
from services.sports_api_client import SportsAPIClient

ODDS_TTL = 43200  # 12 hours

ALLOWED_BOOKMAKERS = {"bet365", "1xbet", "betano"}


class OddsService:
    def __init__(self):
        self.api_client = SportsAPIClient()
        self.r, _ = get_redis_connection()

    def _filter_bookmakers(self, data: list) -> list:
        for entry in data:
            entry["bookmakers"] = [
                b for b in entry.get("bookmakers", [])
                if b.get("name", "").lower() in ALLOWED_BOOKMAKERS
            ]
        return data

    def get_odds_by_fixture(self, fixture_id: int):
        cache_key = f"odds:{fixture_id}"

        if self.r:
            cached = self.r.get(cache_key)
            if cached:
                return {"data": json.loads(cached)}

        data = self.api_client.get_odds_by_fixture(fixture_id)

        if data:
            data = self._filter_bookmakers(data)

        if self.r and data:
            self.r.setex(cache_key, ODDS_TTL, json.dumps(data))

        return {"data": data}

    def get_available_markets(self, fixture_id: int) -> list[str]:
        result = self.get_odds_by_fixture(fixture_id)
        data = result.get("data", [])
        markets = {
            bet["name"]
            for entry in data
            for bookmaker in entry.get("bookmakers", [])
            for bet in bookmaker.get("bets", [])
        }
        return sorted(markets)
