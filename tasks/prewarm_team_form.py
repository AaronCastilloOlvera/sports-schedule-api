import json
import time
from datetime import datetime
from dotenv import load_dotenv
from utils.database import SessionLocal
from utils.redis_client import get_redis_connection
from services.match_service import MatchService
from services.sports_api_client import SportsAPIClient
import pytz

load_dotenv()

RECENT_MATCHES_TTL = 86400  # 24 hours  — team_recent_matches:{team_id}
STATS_TTL = 2592000  # 30 days   — fixture_stats:{fixture_id}


class PrewarmTeamFormWorker:

    def __init__(self):
        self.local_tz = pytz.timezone("America/Mexico_City")
        self.db = SessionLocal()
        self.r, _ = get_redis_connection()
        self.match_service = MatchService(self.db)
        self.api_client = SportsAPIClient()

    def prewarm_recent_matches(self):
        today = datetime.now(self.local_tz).strftime("%Y-%m-%d")
        local_time = datetime.now(self.local_tz).strftime("%H:%M:%S")

        print(f"TEAM FORM 🚀 Local time: {local_time} - Pre-warming team form cache for {today}")

        try:
            matches = self.match_service.get_matches_by_date(today)
            match_list = matches.get("data", [])

            if not match_list:
                print("TEAM FORM - No matches found for today. Skipping.")
                return

            team_ids = {
                team_id
                for match in match_list
                for team_id in (
                    match["teams"]["home"]["id"],
                    match["teams"]["away"]["id"],
                )
            }

            print(f"TEAM FORM - Found {len(team_ids)} unique team(s) across {len(match_list)} match(es).")

            teams_stored  = 0
            teams_failed  = 0
            stats_fetched = 0
            stats_cached  = 0

            for team_id in team_ids:
                # ── Outer loop: one API call per team ──────────────────────────
                fixtures = self.api_client.get_team_recent_form(team_id, last=5)

                if not fixtures:
                    print(f" -> No recent fixtures returned for team {team_id}. Skipping.")
                    teams_failed += 1
                    time.sleep(0.6)  # still rate-limit before the next team call
                    continue

                # ── Inner loop: deep stats per fixture ─────────────────────────
                enriched = []
                for fixture in fixtures:
                    fixture_id = fixture["fixture"]["id"]
                    stats_key  = f"fixture_stats:{fixture_id}"

                    cached = self.r.get(stats_key)
                    if cached:
                        # Historical match — stats never change; skip the API call
                        stats = json.loads(cached)
                        stats_cached += 1
                        print(f"    [cache hit]  fixture_stats:{fixture_id}")
                    else:
                        time.sleep(0.6)  # rate-limit before each uncached stats call
                        stats = self.api_client.get_fixture_statistics(fixture_id)
                        if stats:
                            self.r.setex(stats_key, STATS_TTL, json.dumps(stats))
                        stats_fetched += 1
                        print(f"    [fetched]    fixture_stats:{fixture_id} ({len(stats)} team stat block(s))")

                    enriched.append({**fixture, "statistics": stats})

                # ── Save merged payload ────────────────────────────────────────
                team_key = f"team_recent_matches:{team_id}"
                self.r.setex(team_key, RECENT_MATCHES_TTL, json.dumps(enriched))
                print(f" -> Stored enriched form for team {team_id} ({len(enriched)} fixture(s)) [{team_key}]")
                teams_stored += 1

                time.sleep(0.6)  # rate-limit before the next get_team_recent_form call

            print(
                f"TEAM FORM ✅ Done. "
                f"Teams stored: {teams_stored}, skipped: {teams_failed} | "
                f"Stats fetched: {stats_fetched}, cache hits: {stats_cached}."
            )

        except Exception as e:
            print(f"TEAM FORM 🚨 Error during team form pre-warming: {e}")
        finally:
            self.db.close()
