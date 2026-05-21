import time
import os
from datetime import datetime, timedelta, timezone

import pytz
from dotenv import load_dotenv

from services.match_service import MatchService
from services.notification_service import NotificationService
from services.sports_api_client import SportsAPIClient
from tasks.fixture_builders import FixtureBuilder
from utils.database import SessionLocal
from models.fixture import Fixture

load_dotenv()

FINISHED_STATUSES = {"FT", "AET", "PEN"}
days_for_recent = int(os.getenv("RECENT_MATCHES_DAYS", 2))


class PersistRecentMatchesWorker:

    def __init__(self):
        self.local_tz = pytz.timezone('America/Mexico_City')
        self.api_client = SportsAPIClient()
        self.notification_service = NotificationService()
        self.builder = FixtureBuilder()

    def persist_recent_matches(self):
        local_time = datetime.now(self.local_tz).strftime("%H:%M:%S")
        print(f"PERSIST RECENT 🚀 {local_time} - Persisting last 5 matches per team")

        db = SessionLocal()
        try:
            match_service = MatchService(db)
            today = datetime.now(timezone.utc)

            team_ids = set()
            for i in range(days_for_recent):
                target_date = (today + timedelta(days=i)).strftime("%Y-%m-%d")
                matches = match_service.get_matches_by_date(target_date)
                for match in matches.get("data", []):
                    team_ids.add(match["teams"]["home"]["id"])
                    team_ids.add(match["teams"]["away"]["id"])

            print(f" -> {len(team_ids)} unique team(s) found")

            saved = 0
            skipped = 0

            for team_id in team_ids:
                fixtures = self.api_client.get_team_last_matches(team_id, last=5)
                finished = [
                    f for f in fixtures
                    if f.get("fixture", {}).get("status", {}).get("short") in FINISHED_STATUSES
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
                        saved += 1
                        print(f"  -> Persisted fixture {fixture_id} (team {team_id})")
                    except Exception as e:
                        db.rollback()
                        print(f"  -> ⚠️ Failed to persist fixture {fixture_id}: {e}")

                time.sleep(0.6)

            self.notification_service.send_message(
                f"✅ Task Executed: Recent Matches Persisted ({saved} saved, {skipped} already in db)"
            )
            print(f"PERSIST RECENT ✅ Done. {saved} saved, {skipped} already in db.")

        except Exception as e:
            print(f"PERSIST RECENT 🚨 Error: {e}")
        finally:
            db.close()
