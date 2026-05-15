import time
from datetime import datetime, timedelta

import pytz

from services.match_service import MatchService
from services.notification_service import NotificationService
from services.sports_api_client import SportsAPIClient
from tasks.fixture_builders import FixtureBuilder
from utils.database import SessionLocal
from models.fixture import Fixture

FINISHED_STATUSES = {"FT", "AET", "PEN"}


class PersistFinishedFixturesWorker:

    def __init__(self):
        self.local_tz = pytz.timezone('America/Mexico_City')
        self.notification_service = NotificationService()
        self.api_client = SportsAPIClient()
        self.builder = FixtureBuilder()

    def persist_finished_fixtures(self, date: str = None):
        yesterday = date or (datetime.now(self.local_tz) - timedelta(days=1)).strftime("%Y-%m-%d")
        local_time = datetime.now(self.local_tz).strftime("%H:%M:%S")
        print(f"PERSIST FINISHED 🚀 {local_time} - Persisting finished fixtures for {yesterday}")

        db = SessionLocal()
        try:
            match_service = MatchService(db)
            matches = match_service.get_matches_by_date(yesterday)
            finished = [
                m for m in matches.get("data", [])
                if m.get("fixture", {}).get("status", {}).get("short") in FINISHED_STATUSES
            ]
            print(f" -> {len(finished)} finished fixtures found")

            saved = 0
            skipped = 0

            for match in finished:
                fixture_id = match["fixture"]["id"]
                home_team_id = match["teams"]["home"]["id"]

                if db.query(Fixture).filter(Fixture.id == fixture_id).first():
                    skipped += 1
                    continue

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
                    saved += 1
                    print(f"  -> Persisted fixture {fixture_id}")
                except Exception as e:
                    db.rollback()
                    print(f"  -> ⚠️ Failed to persist fixture {fixture_id}: {e}")

            self.notification_service.send_message(
                f"✅ Task Executed: Finished Fixtures Persisted ({saved} saved, {skipped} skipped)"
            )
            print(f"PERSIST FINISHED ✅ Done. {saved} saved, {skipped} skipped.")

        except Exception as e:
            print(f"PERSIST FINISHED 🚨 Error: {e}")
        finally:
            db.close()
