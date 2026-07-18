import json
from sqlalchemy.orm import Session
from utils.redis_client import get_redis_connection
from services.sports_api_client import SportsAPIClient
from tasks.filters import is_youth_match
import models


class MatchService:
    """Serves the `matches:date:{YYYY-MM-DD}` Redis cache the frontend polls.

    Populated nightly by the prewarm worker and kept fresh during the day by
    `tasks/live_matches.py`. This service only talks to Redis + the live API —
    it never reads persisted `Fixture` rows (that's the nightly persist
    pipeline, a separate concern). `force_refresh` and a plain cache miss both
    resolve the same way: refetch from the API, there is no second data path.
    """

    def __init__(self, db: Session):
      """db: session used only for the favorite-leagues lookup."""
      self.db = db
      self.api_client = SportsAPIClient()
      self.r, _ = get_redis_connection()

    def get_matches_by_date(self, target_date: str, force_refresh: bool = False):
      """Return matches for target_date (YYYY-MM-DD, local calendar day).

      Serves straight from Redis on a cache hit. On a miss, or when
      force_refresh is True, delegates to _refresh_from_api.
      """
      cache_key = f"matches:date:{target_date}"

      if not force_refresh and self.r and self.r.exists(cache_key):
        cached_data = self.r.get(cache_key)
        if cached_data:
          return {"data": json.loads(cached_data)}

      return self._refresh_from_api(target_date, cache_key)

    def _refresh_from_api(self, target_date: str, cache_key: str):
      """Fetch fixtures live, filter to favorite leagues, merge in any events
      already cached by the live worker, and rewrite the Redis cache.

      TODO: no retry/backoff on API failure — SportsAPIClient swallows errors
      and returns [], so a transient API outage looks identical to "no
      matches today" and silently overwrites a previously-good cache (see H1
      / H3 in CLAUDE.md).
      TODO: postponed/cancelled fixtures outside the live-tracking window
      (calculate_live_windows) never get their status refreshed mid-day —
      nothing currently detects a PST/CANC transition until the next nightly
      prewarm or a manual force_refresh.
      """
      all_matches = self.api_client.get_fixtures_by_date(target_date)

      favorite_leagues = self.db.query(models.League).filter(models.League.is_favorite == True).all()
      favorite_ids = {league.id for league in favorite_leagues}

      filtered_matches = [
          m for m in all_matches
          if m.get("league", {}).get("id") in favorite_ids
          and not is_youth_match(m)
      ]

      if self.r:
        # Preserve events merged by the live worker
        existing_raw = self.r.get(cache_key)
        if existing_raw:
          existing_by_id = {m["fixture"]["id"]: m for m in json.loads(existing_raw)}
          for match in filtered_matches:
            fid = match["fixture"]["id"]
            if fid in existing_by_id and existing_by_id[fid].get("events"):
              match["events"] = existing_by_id[fid]["events"]

        self.r.setex(cache_key, 432000, json.dumps(filtered_matches))

      return {"data": filtered_matches}