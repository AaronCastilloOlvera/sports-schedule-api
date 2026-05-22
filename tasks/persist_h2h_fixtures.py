import time
import os
from datetime import datetime, timedelta, timezone

import pytz
from dotenv import load_dotenv

from services.match_service import MatchService
from services.notification_service import NotificationService
from services.sports_api_client import SportsAPIClient
from tasks.fixture_builders import FixtureBuilder
from tasks.filters import is_youth_match
from utils.database import SessionLocal
from models.fixture import Fixture

load_dotenv()

FINISHED_STATUSES = {"FT", "AET", "PEN"}
days_for_h2h = int(os.getenv("H2H_DAYS", 2))


class PersistH2HFixturesWorker:

    def __init__(self):
        self.local_tz = pytz.timezone('America/Mexico_City')
        self.api_client = SportsAPIClient()
        self.notification_service = NotificationService()
        self.builder = FixtureBuilder()

    def persist_h2h_fixtures(self):
        local_time = datetime.now(self.local_tz).strftime("%H:%M:%S")
        print(f"PERSIST H2H 🚀 {local_time} - Persisting H2H fixtures for upcoming matches")

        db = SessionLocal()
        try:
            match_service = MatchService(db)
            today = datetime.now(timezone.utc)

            pairs_seen = set()
            saved = 0
            skipped = 0

            for i in range(days_for_h2h):
                target_date = (today + timedelta(days=i)).strftime("%Y-%m-%d")
                matches = match_service.get_matches_by_date(target_date)

                for match in matches.get("data", []):
                    home_id = match["teams"]["home"]["id"]
                    away_id = match["teams"]["away"]["id"]
                    pair = tuple(sorted([home_id, away_id]))

                    if pair in pairs_seen:
                        continue
                    pairs_seen.add(pair)

                    pair_saved, pair_skipped = self._persist_pair(db, pair[0], pair[1])
                    saved += pair_saved
                    skipped += pair_skipped
                    time.sleep(0.5)

            self.notification_service.send_message(
                f"✅ Task Executed: H2H Fixtures Persisted ({saved} saved, {skipped} already in db)"
            )
            print(f"PERSIST H2H ✅ Done. {saved} saved, {skipped} already in db.")

        except Exception as e:
            print(f"PERSIST H2H 🚨 Error: {e}")
        finally:
            db.close()

    def _persist_pair(self, db, team1: int, team2: int) -> tuple[int, int]:
        saved = 0
        skipped = 0

        h2h_fixtures = self.api_client.get_headtohead_matches(team1, team2, last=10)
        finished = [
            f for f in h2h_fixtures
            if f.get("fixture", {}).get("status", {}).get("short") in FINISHED_STATUSES
            and not is_youth_match(f)
        ]

        for match in finished:
            fixture_id = match["fixture"]["id"]

            if db.query(Fixture).filter(Fixture.id == fixture_id).first():
                skipped += 1
                continue

            home_team_id = match["teams"]["home"]["id"]

            time.sleep(0.6)
            raw_stats = self.api_client.get_fixture_statistics(fixture_id)
            time.sleep(0.6)
            raw_events = self.api_client.get_fixture_events(fixture_id)
            time.sleep(0.6)
            raw_lineups = self.api_client.get_fixture_lineups(fixture_id)
            time.sleep(0.6)
            raw_player_stats = self.api_client.get_fixture_player_statistics(fixture_id)

            try:
                db.add(self.builder.build_fixture(match))
                if raw_stats:
                    for row in self.builder.build_team_stats(fixture_id, home_team_id, raw_stats):
                        db.add(row)
                if raw_events:
                    for row in self.builder.build_events(fixture_id, raw_events):
                        db.add(row)
                if raw_lineups:
                    for row in self.builder.build_lineups(fixture_id, raw_lineups):
                        db.add(row)
                if raw_player_stats:
                    for row in self.builder.build_player_stats(fixture_id, raw_player_stats):
                        db.add(row)
                db.commit()
                db.expunge_all()
                saved += 1
                print(f"  -> Persisted H2H fixture {fixture_id}")
            except Exception as e:
                db.rollback()
                print(f"  -> ⚠️ Failed to persist H2H fixture {fixture_id}: {e}")

        return saved, skipped
