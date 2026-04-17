from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from utils.database import SessionLocal
from services.match_service import MatchService
from services.H2HService import H2HService
from services.notification_service import NotificationService
import time
import os
import pytz

load_dotenv()
days_to_prewarm = int(os.getenv("DAYS_TO_PREWARM", 5))

# =================================================
# This script is used to prewarm the Redis cache.
# =================================================

class PrewarmCacheWorker:
  
  def __init__(self):
    self.local_tz = pytz.timezone('America/Mexico_City')
    self.db = SessionLocal()
    self.notification_service = NotificationService()
    self.h2h_service = H2HService()
    self.match_service = MatchService(self.db)
    self.today = datetime.now(timezone.utc)

  def prewarm_matches(self):
    local_time = datetime.now(self.local_tz).strftime("%H:%M:%S")

    print(f"PREWARM 🚀 Local time: {local_time} - Prewarming cache for next {days_to_prewarm} day(s) starting"  )

    total_matches_stored = 0
    count_by_league = {}

    try:

      for i in range(days_to_prewarm):
        target_date = (self.today + timedelta(days=i)).strftime("%Y-%m-%d")
        matches = self.match_service.get_matches_by_date(target_date, force_refresh=True)
        total_matches_stored += len(matches.get("data", []))
        print(f"Fetched {len(matches)} matches for date: {target_date}")

        time.sleep(0.5)  # Sleep to avoid overwhelming the database or API

        # Prewarm H2H cache
        for match in matches.get("data", []):
          self.h2h_service.get_headtohead_matches(match["teams"]["home"]["id"], match["teams"]["away"]["id"])
          league_name = match["league"]["name"]

          if league_name in count_by_league:
            count_by_league[league_name] += 1
          else:
            count_by_league[league_name] = 1
          
          print(f" -> {str(count_by_league[league_name])} matches for league: {league_name}")
          time.sleep(0.5)  # Sleep to avoid overwhelming the API

      # Build summary message
      message_html = (
        f"✅ <b> Cache Prewarming Summary</b>\n"
        f"⚽ Total Matches Stored: {total_matches_stored}\n\n"
        f"🏆 <b> Matches by League:</b>\n"
      )
      
      for league, count in count_by_league.items():
        message_html += f"{league}: {count}\n"
      
      self.notification_service.send_message(message_html)
      print(message_html)
      
    except Exception as e:
      print(f"🚨Error during cache prewarming: {str(e)}")
    finally:
      self.db.close()