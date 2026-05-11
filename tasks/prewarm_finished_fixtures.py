import json
import time
from datetime import datetime, timedelta

import pytz

from services.sports_api_client import SportsAPIClient
from services.match_service import MatchService
from services.notification_service import NotificationService
from utils.database import SessionLocal
from utils.redis_client import get_redis_connection

FINISHED_STATUSES = {"FT", "AET", "PEN"}
STATS_TTL = 2592000   # 30 days — matches existing fixture_stats TTL
EVENTS_TTL = 172800   # 48 hours
LINEUPS_TTL = 172800  # 48 hours
PLAYER_STATS_TTL = 172800  # 48 hours


class PrewarmFinishedFixturesWorker:

    def __init__(self):
        self.local_tz = pytz.timezone('America/Mexico_City')
        self.api_client = SportsAPIClient()
        self.notification_service = NotificationService()

    def prewarm_finished_fixtures(self, date: str = None):
        yesterday = date or (datetime.now(self.local_tz) - timedelta(days=1)).strftime("%Y-%m-%d")
        local_time = datetime.now(self.local_tz).strftime("%H:%M:%S")
        print(f"PREWARM FINISHED 🚀 {local_time} - Fetching data for finished fixtures on {yesterday}")

        r, _ = get_redis_connection()
        if not r:
            print("PREWARM FINISHED 🚨 Redis unavailable, skipping.")
            return

        db = SessionLocal()
        try:
            match_service = MatchService(db)
            matches = match_service.get_matches_by_date(yesterday)
            finished = [
                m for m in matches.get("data", [])
                if m.get("fixture", {}).get("status", {}).get("short") in FINISHED_STATUSES
            ]
            print(f" -> {len(finished)} finished fixtures found")

            for match in finished:
                fixture_id = match["fixture"]["id"]
                home_team_id = match["teams"]["home"]["id"]
                away_team_id = match["teams"]["away"]["id"]

                # statistics — might already be cached 30 days by routes/teams.py
                stats_key = f"fixture_stats:{fixture_id}"
                if not r.exists(stats_key):
                    data = self.api_client.get_fixture_statistics(fixture_id)
                    if data is not None:
                        r.setex(stats_key, STATS_TTL, json.dumps(data))
                    time.sleep(0.6)

                # events
                events_key = f"fixture_events:{fixture_id}"
                if not r.exists(events_key):
                    data = self.api_client.get_fixture_events(fixture_id)
                    if data is not None:
                        r.setex(events_key, EVENTS_TTL, json.dumps(data))
                    time.sleep(0.6)

                # lineups
                lineups_key = f"fixture_lineups:{fixture_id}"
                if not r.exists(lineups_key):
                    data = self.api_client.get_fixture_lineups(fixture_id)
                    if data is not None:
                        r.setex(lineups_key, LINEUPS_TTL, json.dumps(data))
                    time.sleep(0.6)

                # player stats — 1 call, returns both teams
                player_stats_key = f"fixture_player_stats:{fixture_id}"
                if not r.exists(player_stats_key):
                    data = self.api_client.get_fixture_player_statistics(fixture_id)
                    if data is not None:
                        r.setex(player_stats_key, PLAYER_STATS_TTL, json.dumps(data))
                    time.sleep(0.6)

                print(f"  -> Prewarmed fixture {fixture_id}")

            self.notification_service.send_message(
                f"✅ Task Executed: Finished Fixtures Prewarmed ({len(finished)} fixtures)"
            )
            print("PREWARM FINISHED ✅ Done.")

        except Exception as e:
            print(f"PREWARM FINISHED 🚨 Error: {e}")
        finally:
            db.close()
