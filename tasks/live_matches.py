import json
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from dateutil import parser
from utils.database import SessionLocal
from utils.redis_client import get_redis_connection
from services.match_service import MatchService
from services.sports_api_client import SportsAPIClient
import models
import os
import pytz

load_dotenv()

MATCHES_DATE_TTL = 432000  # 5 days — matches:date:{YYYY-MM-DD}


class LiveWorker:
  def __init__(self):
    self.local_tz = pytz.timezone('America/Mexico_City')
    self.active_windows = []
    self.active_games_pending = False
    self.live_fixture_ids = set()  # IDs tracked as in-play last cycle
    self.IN_PLAY_STATUSES = {'1H', 'HT', '2H', 'ET', 'BT', 'P', 'LIVE', 'INT'}
    self.api_client = SportsAPIClient()

  def calculate_live_windows(self):
    """Calculates today's match schedules and creates active time windows."""
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

      windows = []
      for match in matches:
          start_time = parser.parse(match["fixture"]["date"])
          start_local = start_time.astimezone(self.local_tz)
          if start_local.strftime("%Y-%m-%d") != today_str:
              continue
          windows.append([start_time, start_time + timedelta(hours=3)])

      windows.sort(key=lambda x: x[0])
      merged_windows = []
      for w in windows:
          if not merged_windows:
              merged_windows.append(w)
          else:
              last_window = merged_windows[-1]
              if w[0] <= last_window[1]:
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
    """
    Fetches /fixtures?live=all, filters to favorite leagues, and merges rich
    match data (events, statistics, score, status) into the existing Redis
    schedule cache without overwriting base schedule fields.
    """
    current_time_utc = datetime.now(timezone.utc)
    is_scheduled_time = any(start <= current_time_utc <= end for start, end in self.active_windows)

    if not self.active_windows:
      print("[WORKER] 💤 No matches today. Worker will sleep all day.")
      return

    if not (is_scheduled_time or self.active_games_pending):
      print(f"[WORKER] 💤 Outside active windows (UTC {current_time_utc.strftime('%H:%M')}). Skipping.")
      return

    print(f"[WORKER] ⚽ Active window (UTC {current_time_utc.strftime('%H:%M:%S')}). Fetching live data...")

    r, redis_error = get_redis_connection()
    if r is None:
      print(f"[WORKER] ⚠️ Redis unavailable: {redis_error}. Skipping update.")
      return

    db = SessionLocal()
    try:
      # ── 1. Restore live_fixture_ids from Redis if process just restarted ─────
      if not self.live_fixture_ids:
        raw_ids = r.get("live:tracking_ids")
        if raw_ids:
          self.live_fixture_ids = set(json.loads(raw_ids))
          print(f"[WORKER] 🔄 Restored tracking state from Redis: {self.live_fixture_ids}")

      # ── 2. Resolve favorite league IDs from DB ─────────────────────────────
      favorite_ids = {
        league.id
        for league in db.query(models.League).filter(models.League.is_favorite == True).all()
      }

      # ── 3. Fetch all globally live fixtures ────────────────────────────────
      live_fixtures = self.api_client.get_live_fixtures()

      # ── 4. Filter to favorite leagues only ─────────────────────────────────
      live_favorites = [
        f for f in live_fixtures
        if f.get("league", {}).get("id") in favorite_ids
      ]

      print(f"[WORKER] 📡 {len(live_fixtures)} live globally, {len(live_favorites)} in favorite leagues.")

      # ── 5. Update active_games_pending and detect finished fixtures ─────────
      current_ids = {f["fixture"]["id"] for f in live_favorites}
      finished_ids = self.live_fixture_ids - current_ids  # were live, now gone

      self.live_fixture_ids = current_ids
      r.setex("live:tracking_ids", 600, json.dumps(list(current_ids)))

      self.active_games_pending = any(
        f.get("fixture", {}).get("status", {}).get("short") in self.IN_PLAY_STATUSES
        for f in live_favorites
      )

      # Some fixtures dropped out of the live feed (finished) — force-refresh
      # the date cache so their final FT status is written to Redis.
      if finished_ids:
        print(f"[WORKER] 🏁 {len(finished_ids)} fixture(s) just finished {finished_ids}. Force-refreshing FT status...")
        match_service = MatchService(db)
        today_str = datetime.now(self.local_tz).strftime("%Y-%m-%d")
        match_service.get_matches_by_date(today_str, force_refresh=True)
        print("[WORKER] ✅ FT status captured.")

      if not live_favorites:
        print("[WORKER] 💤 No live matches in favorite leagues.")
        return

      # ── 6. Merge rich data into existing Redis schedule ────────────────────
      today_str = datetime.now(self.local_tz).strftime("%Y-%m-%d")
      cache_key = f"matches:date:{today_str}"

      raw = r.get(cache_key)
      if raw is None:
        print(f"[WORKER] ⚠️ No base schedule in Redis for {today_str}. Cannot merge.")
        return

      base_matches = {m["fixture"]["id"]: m for m in json.loads(raw)}

      updated = 0
      for live_match in live_favorites:
        fid = live_match["fixture"]["id"]
        if fid not in base_matches:
          continue

        base = base_matches[fid]
        # Overwrite status/elapsed but keep static base fields (date, venue, referee).
        base["fixture"] = {**base["fixture"], **live_match["fixture"]}
        base["goals"]      = live_match.get("goals",      base.get("goals"))
        base["score"]      = live_match.get("score",      base.get("score"))
        base["events"]     = live_match.get("events",     [])
        base["statistics"] = live_match.get("statistics", [])
        updated += 1

      # Preserve whatever TTL remains; fall back to the standard 5-day TTL.
      remaining_ttl = r.ttl(cache_key)
      ttl = remaining_ttl if remaining_ttl > 0 else MATCHES_DATE_TTL
      r.setex(cache_key, ttl, json.dumps(list(base_matches.values())))

      print(f"[WORKER] ✅ Merged live data for {updated} fixture(s) into {cache_key}.")
      print(f"[WORKER] Active games still pending: {self.active_games_pending}")

    except Exception as e:
      print(f"[WORKER ERROR] ❌ {str(e)}")
    finally:
      db.close()
