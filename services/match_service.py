import json
from sqlalchemy.orm import Session
from utils.redis_client import get_redis_connection
from services.sports_api_client import SportsAPIClient
import models

class MatchService:
    def __init__(self, db: Session):
      self.db = db
      self.api_client = SportsAPIClient()
      self.r, _ = get_redis_connection()

    def get_matches_by_date(self, target_date: str, force_refresh: bool = False):
      """
      Fetches matches for a specific date. 
      If force_refresh is True, it will fetch fresh data from the API and update the cache.
      """
      cache_key = f"matches_date_{target_date}"

      if not force_refresh and self.r.exists(cache_key):
        cached_data = self.r.get(cache_key)
        if cached_data:
          return { "data": json.loads(cached_data) }

      all_matches = self.api_client.get_fixtures_by_date(target_date)

      # Filter matches by favorite leagues
      favorite_leagues = self.db.query(models.League).filter(models.League.is_favorite == True).all()
      favorite_ids = {league.id for league in favorite_leagues}
      
      filtered_matches = [
          m for m in all_matches 
          if m.get("league", {}).get("id") in favorite_ids
      ]

      if self.r:
        self.r.setex(cache_key, 432000, json.dumps(filtered_matches))  # Cache for 5 days

      return { "data": filtered_matches }