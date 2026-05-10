from datetime import datetime
from utils.database import SessionLocal
from services.match_service import MatchService
from services.odds_service import OddsService
from services.notification_service import NotificationService
import time
import pytz


class PrewarmOddsWorker:

    def __init__(self):
        self.local_tz = pytz.timezone('America/Mexico_City')
        self.odds_service = OddsService()
        self.notification_service = NotificationService()

    def prewarm_odds(self):
        """Reads today's cached matches and fetches odds for every upcoming fixture."""
        db = SessionLocal()
        try:
            match_service = MatchService(db)
            today = datetime.now(self.local_tz).strftime("%Y-%m-%d")
            local_time = datetime.now(self.local_tz).strftime("%H:%M:%S")
            print(f"PREWARM ODDS 🚀 {local_time} - Caching odds for {today}")

            # Match schedules are already in Redis from prewarm_match_schedules — no API call here.
            matches = match_service.get_matches_by_date(today)
            upcoming = [
                m for m in matches.get("data", [])
                if m.get("fixture", {}).get("status", {}).get("short") == "NS"
            ]

            print(f" -> {len(upcoming)} upcoming matches found")

            for match in upcoming:
                fixture_id = match["fixture"]["id"]
                self.odds_service.get_odds_by_fixture(fixture_id)
                print(f"  -> Cached odds for fixture {fixture_id}")
                time.sleep(0.6)

            self.notification_service.send_message("✅ Task Executed: Odds Data Cached")
            print("PREWARM ODDS ✅ Done.")

        except Exception as e:
            print(f"PREWARM ODDS 🚨 Error: {e}")
        finally:
            db.close()
