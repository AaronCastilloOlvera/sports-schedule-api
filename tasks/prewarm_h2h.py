import time
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from utils.database import SessionLocal
from services.match_service import MatchService
from services.notification_service import NotificationService
import os
import pytz

load_dotenv()
days_to_prewarm = int(os.getenv("DAYS_TO_PREWARM", 5))


class PrewarmCacheWorker:

    def __init__(self):
        self.local_tz = pytz.timezone('America/Mexico_City')
        self.notification_service = NotificationService()

    def prewarm_match_schedules(self):
        db = SessionLocal()
        try:
            match_service = MatchService(db)
            today = datetime.now(timezone.utc)
            local_time = datetime.now(self.local_tz).strftime("%H:%M:%S")
            print(f"PREWARM MATCHES 🚀 {local_time} - Caching schedules for next {days_to_prewarm} day(s)")

            for i in range(days_to_prewarm):
                target_date = (today + timedelta(days=i)).strftime("%Y-%m-%d")
                matches = match_service.get_matches_by_date(target_date, force_refresh=True)
                print(f" -> {len(matches.get('data', []))} matches for {target_date}")
                time.sleep(0.5)

            self.notification_service.send_message("✅ Task Executed: Today's Matches Cached")
            print("PREWARM MATCHES ✅ Done.")

        except Exception as e:
            print(f"PREWARM MATCHES 🚨 Error: {e}")
        finally:
            db.close()
