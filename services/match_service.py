import json
from sqlalchemy.orm import Session
from sqlalchemy import cast, Date
from utils.redis_client import get_redis_connection
from services.sports_api_client import SportsAPIClient
from tasks.filters import is_youth_match
from models.fixture import Fixture
from models.fixture_team_stats import FixtureTeamStats
from utils.fixture_serializer import serialize_fixture
import models

class MatchService:
    def __init__(self, db: Session):
      self.db = db
      self.api_client = SportsAPIClient()
      self.r, _ = get_redis_connection()

    def get_matches_by_date(self, target_date: str, force_refresh: bool = False):
      cache_key = f"matches:date:{target_date}"

      if not force_refresh and self.r and self.r.exists(cache_key):
        cached_data = self.r.get(cache_key)
        if cached_data:
          return {"data": json.loads(cached_data)}

      if force_refresh:
        return self._refresh_from_db(target_date, cache_key)

      # Cache miss on regular request — fall back to API
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

    def _refresh_from_db(self, target_date: str, cache_key: str):
      favorite_leagues = self.db.query(models.League).filter(models.League.is_favorite == True).all()
      favorite_ids = {league.id for league in favorite_leagues}

      fixtures = (
        self.db.query(Fixture)
        .filter(
          cast(Fixture.date_utc, Date) == target_date,
          Fixture.league_id.in_(favorite_ids),
        )
        .order_by(Fixture.date_utc)
        .all()
      )

      if not fixtures:
        # No DB data — try existing Redis cache first
        if self.r:
          existing = self.r.get(cache_key)
          if existing:
            return {"data": json.loads(existing)}
        # BD and Redis both empty — fall back to API as last resort
        return self._refresh_from_api(target_date, cache_key)

      league_ids = {f.league_id for f in fixtures if f.league_id}
      leagues = {
        l.id: l.name
        for l in self.db.query(models.League).filter(models.League.id.in_(league_ids)).all()
      }

      serialized = []
      for fixture in fixtures:
        stats = (
          self.db.query(FixtureTeamStats)
          .filter(FixtureTeamStats.fixture_id == fixture.id)
          .all()
        )
        serialized.append(serialize_fixture(fixture, stats, leagues.get(fixture.league_id)))

      if self.r:
        self.r.setex(cache_key, 432000, json.dumps(serialized))

      return {"data": serialized}

    def _refresh_from_api(self, target_date: str, cache_key: str):
      all_matches = self.api_client.get_fixtures_by_date(target_date)

      favorite_leagues = self.db.query(models.League).filter(models.League.is_favorite == True).all()
      favorite_ids = {league.id for league in favorite_leagues}

      filtered_matches = [
          m for m in all_matches
          if m.get("league", {}).get("id") in favorite_ids
          and not is_youth_match(m)
      ]

      if self.r and filtered_matches:
        self.r.setex(cache_key, 432000, json.dumps(filtered_matches))

      return {"data": filtered_matches}