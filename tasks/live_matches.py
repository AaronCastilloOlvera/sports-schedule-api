from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from dateutil import parser
from utils.database import SessionLocal
from services.match_service import MatchService
import os
import pytz

load_dotenv()

class LiveWorker:
  def __init__(self):
    # We keep your local timezone specifically for fetching "Today's" schedule correctly
    self.local_tz = pytz.timezone('America/Mexico_City')
    self.active_windows = []
    self.active_games_pending = False
    self.IN_PLAY_STATUSES = {'1H', 'HT', '2H', 'ET', 'BT', 'P', 'LIVE', 'INT'}

  def calculate_live_windows(self):
    """Calculates today's match schedules and creates active time windows"""
    print("[WINDOWS] 🕵️‍♂️ Searching for today's matches...")
    db = SessionLocal()
    try:
      match_service = MatchService(db)
      today_str = datetime.now(self.local_tz).strftime("%Y-%m-%d")
      
      response = match_service.get_matches_by_date(today_str)
      matches = response.get("data", [])
      
      if not matches:
          print("[WINDOWS] 💤 No matches scheduled for today.")
          self.active_windows = []
          return

        # Calculate windows (start time + 3 hours buffer for extra time/penalties)
      windows = []
      for match in matches:
          start_time = parser.parse(match["fixture"]["date"])
          
          start_local = start_time.astimezone(self.local_tz)
          if start_local.strftime("%Y-%m-%d") != today_str:
              continue
             
          windows.append([start_time, start_time + timedelta(hours=3)])
          
      # Algorithm: Merge overlapping windows
      windows.sort(key=lambda x: x[0])
      merged_windows = []
      for w in windows:
          if not merged_windows:
              merged_windows.append(w)
          else:
              last_window = merged_windows[-1]
              if w[0] <= last_window[1]: # Overlapping condition
                  last_window[1] = max(last_window[1], w[1])
              else:
                  merged_windows.append(w)
                  
      self.active_windows = merged_windows
      print(f"[WINDOWS] ✅ Planning complete. {len(merged_windows)} active windows for today.")

      total_active_minutes = 0
      for start_time, end_time in self.active_windows:
        duration = (end_time - start_time).total_seconds() / 60
        total_active_minutes += duration
         
        start_local = start_time.astimezone(self.local_tz).strftime("%H:%M")
        end_local = end_time.astimezone(self.local_tz).strftime("%H:%M")
        print(f"[WINDOWS] 🕒 ActiveWindow: {start_local} - {end_local} (Local Time)")

      minutes_interval = int(os.getenv("WORKER_INTERVAL_MINUTES", 5))
      estimated_requests = int(total_active_minutes / minutes_interval)

      print(f"\n[WINDOWS] " + "-"*40)
      print(f"[WINDOWS] 📈 DAILY API CONSUMPTION ESTIMATE 📈")
      print(f"[WINDOWS] " + "-"*40)
      print(f"[WINDOWS] ⏱️ Refresh Interval : Every {minutes_interval} minutes")
      print(f"[WINDOWS] ⏳ Total Active Time: {int(total_active_minutes // 60)}h {int(total_active_minutes % 60)}m")
      print(f"[WINDOWS] 📡 Estimated Requests: ~{estimated_requests} API calls")
      print(f"[WINDOWS] " + "-"*40 + "\n")
      

    except Exception as e:
        print(f"[WINDOWS ERROR] ❌ {str(e)}")
    finally:
        db.close()

  def run_live_update(self):
    """Checks the API if we are within an active window or games are still pending"""
    current_time_utc = datetime.now(timezone.utc)
    is_scheduled_time = any(start <= current_time_utc <= end for start, end in self.active_windows)
    
    if not self.active_windows:
      print("[WORKER] 💤 No matches today. Worker will sleep all day.")
      return

    if is_scheduled_time or self.active_games_pending:
      print(f"[WORKER] ⚽ In-play window active (Current UTC: {current_time_utc.strftime('%H:%M:%S')}). Fetching data...")
      db = SessionLocal()
      try:
        match_service = MatchService(db)
        today_str = datetime.now(self.local_tz).strftime("%Y-%m-%d")
        
        # Bypasses Redis cache, fetches from API, and updates Redis
        response = match_service.get_matches_by_date(today_str, force_refresh=True)
        matches = response.get("data", [])
        
        # Check if ANY match is currently being played
        self.active_games_pending = any(
            m.get("fixture", {}).get("status", {}).get("short") in self.IN_PLAY_STATUSES
            for m in matches
        )
        print(f"[WORKER] ✅ Update finished. Pending active games: {self.active_games_pending}")
          
      except Exception as e:
          print(f"[WORKER ERROR] ❌ {str(e)}")
      finally:
          db.close()