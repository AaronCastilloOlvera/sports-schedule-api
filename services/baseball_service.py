import json
import time
from datetime import datetime

from utils.redis_client import get_redis_connection
from services.mlb_api_client import MLBApiClient
from services.mlb_radar_service import build_espn_pitcher_splits

REQUEST_SLEEP = 0.3  # ESPN endpoint no es oficial — misma cortesía que mlb_radar_service

SCHEDULE_TTL = 120     # 2 min — live scores change frequently
BOXSCORE_TTL = 120     # 2 min while live; completed games rarely re-fetched
GAMELOG_TTL = 21600    # 6h — same cadence, one new row appears every ~5 days
FINAL_SCORE_TTL = 604800  # 7d — a completed game's score never changes

# MLB only -- LMB dropped, ESPN has no LMB coverage (both `.../baseball/lmb`
# and `.../mexican-league` return 400).
#
# Every method returns ESPN's shape untouched -- except get_pitcher_game_log,
# which converts to the old MLB-Stats-API `split` shape via
# build_espn_pitcher_splits() so the frontend's existing gamelog UI didn't
# need to change.


class BaseballService:
    def __init__(self):
        self.client = MLBApiClient()
        self.r, _ = get_redis_connection()

    def get_schedule(self, date: str, force_refresh: bool = False) -> dict:
        """Raw ESPN scoreboard `events` list for one day (YYYY-MM-DD)."""
        cache_key = f"baseball:mlb:{date}"
        if not force_refresh and self.r:
            cached = self.r.get(cache_key)
            if cached:
                return {"data": json.loads(cached)}

        data = self.client.get_scoreboard(date.replace("-", ""))
        events = data.get("events") or []
        if self.r and events:
            self.r.setex(cache_key, SCHEDULE_TTL, json.dumps(events))
        return {"data": events}

    def get_boxscore(self, game_pk) -> dict:
        """Raw ESPN `summary` for one event -- header/boxscore/linescores/pickcenter."""
        cache_key = f"baseball:boxscore:{game_pk}"
        if self.r:
            cached = self.r.get(cache_key)
            if cached:
                return {"data": json.loads(cached)}

        box = self.client.get_summary(game_pk)
        if self.r and box:
            self.r.setex(cache_key, BOXSCORE_TTL, json.dumps(box))
        return {"data": box}

    def get_pitcher_stats(self, person_id: int) -> dict:
        """ESPN has no season-totals endpoint -- this is just the current-season gamelog."""
        return self.get_pitcher_game_log(person_id, seasons=1)

    def get_pitcher_game_log(self, person_id: int, seasons: int = 5) -> dict:
        """
        Flat list of `split`-shaped starts across `seasons` years (old
        MLB-Stats-API contract, see module docstring). ESPN's gamelog is
        single-season, so one call per year, converted via
        build_espn_pitcher_splits() and concatenated.
        """
        current_year = datetime.now().year
        years = list(range(current_year, current_year - seasons, -1))

        cache_key = f"baseball:pitcher-gamelog:mlb:{person_id}:{seasons}"
        if self.r:
            cached = self.r.get(cache_key)
            if cached:
                return {"data": json.loads(cached)}

        splits = []
        for i, year in enumerate(years):
            if i > 0:
                time.sleep(REQUEST_SLEEP)
            raw = self.client.get_pitcher_gamelog(person_id, year)
            if raw:
                splits.extend(build_espn_pitcher_splits(raw, {}))

        if self.r and splits:
            self.r.setex(cache_key, GAMELOG_TTL, json.dumps(splits))
        return {"data": splits}

    def get_games_final_scores(self, game_pks: list) -> dict:
        """{game_pk: summary.header.competitions[0]} -- lighter than the full summary."""
        scores = {}
        for game_pk in game_pks:
            cache_key = f"baseball:final-score:{game_pk}"
            cached = self.r.get(cache_key) if self.r else None
            if cached:
                scores[game_pk] = json.loads(cached)
                continue

            summary = self.client.get_summary(game_pk)
            comp = ((summary.get("header") or {}).get("competitions") or [None])[0]
            if comp:
                scores[game_pk] = comp
                if self.r:
                    self.r.setex(cache_key, FINAL_SCORE_TTL, json.dumps(comp))
        return scores
