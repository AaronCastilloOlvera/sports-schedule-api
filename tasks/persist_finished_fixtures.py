import json
from datetime import datetime

import pytz

from services.match_service import MatchService
from services.notification_service import NotificationService
from utils.database import SessionLocal
from utils.redis_client import get_redis_connection
from models.fixture import Fixture
from models.fixture_team_stats import FixtureTeamStats
from models.fixture_event import FixtureEvent
from models.fixture_lineup import FixtureLineup
from models.fixture_player_stats import FixturePlayerStats

FINISHED_STATUSES = {"FT", "AET", "PEN"}


class PersistFinishedFixturesWorker:

    def __init__(self):
        self.local_tz = pytz.timezone('America/Mexico_City')
        self.notification_service = NotificationService()
        self.r, _ = get_redis_connection()

    # ------------------------------------------------------------------ helpers

    def _get_stat(self, stats_list, stat_type):
        """Extract an integer stat from the API-Sports statistics array."""
        for s in stats_list:
            if s.get("type") == stat_type:
                val = s.get("value")
                if val is None:
                    return None
                if isinstance(val, str):
                    val = val.replace("%", "").strip()
                    if not val:
                        return None
                try:
                    return int(float(str(val)))
                except (ValueError, TypeError):
                    return None
        return None

    def _get_stat_decimal(self, stats_list, stat_type):
        """Extract a decimal stat (e.g. xG) from the API-Sports statistics array."""
        for s in stats_list:
            if s.get("type") == stat_type:
                val = s.get("value")
                if val is None:
                    return None
                try:
                    return float(str(val))
                except (ValueError, TypeError):
                    return None
        return None

    def _safe_int(self, val):
        if val is None:
            return None
        try:
            return int(val)
        except (ValueError, TypeError):
            return None

    def _safe_decimal(self, val):
        if val is None:
            return None
        try:
            return float(str(val))
        except (ValueError, TypeError):
            return None

    # ------------------------------------------------------------------ builders

    def _build_fixture(self, match):
        f = match["fixture"]
        score = match.get("score", {})
        ht = score.get("halftime", {})
        date_str = f.get("date")
        date_utc = datetime.fromisoformat(date_str) if date_str else None

        return Fixture(
            id=f["id"],
            league_id=match["league"].get("id"),
            season=match["league"].get("season"),
            date_utc=date_utc,
            home_team_id=match["teams"]["home"]["id"],
            home_team_name=match["teams"]["home"]["name"],
            away_team_id=match["teams"]["away"]["id"],
            away_team_name=match["teams"]["away"]["name"],
            home_goals=match["goals"].get("home"),
            away_goals=match["goals"].get("away"),
            home_goals_ht=ht.get("home"),
            away_goals_ht=ht.get("away"),
            status=f["status"]["short"],
            venue_name=f.get("venue", {}).get("name"),
            referee=f.get("referee"),
        )

    def _build_team_stats(self, fixture_id, home_team_id, raw_stats):
        rows = []
        for entry in raw_stats:
            team = entry.get("team", {})
            stats = entry.get("statistics", [])
            rows.append(FixtureTeamStats(
                fixture_id=fixture_id,
                team_id=team["id"],
                team_name=team["name"],
                is_home=(team["id"] == home_team_id),
                shots_total=self._get_stat(stats, "Total Shots"),
                shots_on_target=self._get_stat(stats, "Shots on Goal"),
                shots_off_target=self._get_stat(stats, "Shots off Goal"),
                shots_blocked=self._get_stat(stats, "Blocked Shots"),
                shots_inside_box=self._get_stat(stats, "Shots insidebox"),
                shots_outside_box=self._get_stat(stats, "Shots outsidebox"),
                possession=self._get_stat(stats, "Ball Possession"),
                passes_total=self._get_stat(stats, "Total passes"),
                passes_accurate=self._get_stat(stats, "Passes accurate"),
                passes_accuracy=self._get_stat(stats, "Passes %"),
                fouls=self._get_stat(stats, "Fouls"),
                corners=self._get_stat(stats, "Corner Kicks"),
                offsides=self._get_stat(stats, "Offsides"),
                yellow_cards=self._get_stat(stats, "Yellow Cards"),
                red_cards=self._get_stat(stats, "Red Cards"),
                saves=self._get_stat(stats, "Goalkeeper Saves"),
                expected_goals=self._get_stat_decimal(stats, "expected_goals"),
            ))
        return rows

    def _build_events(self, fixture_id, raw_events):
        rows = []
        for e in raw_events:
            rows.append(FixtureEvent(
                fixture_id=fixture_id,
                minute=self._safe_int(e.get("time", {}).get("elapsed")),
                extra_minute=self._safe_int(e.get("time", {}).get("extra")),
                team_id=e.get("team", {}).get("id"),
                team_name=e.get("team", {}).get("name"),
                player_id=e.get("player", {}).get("id"),
                player_name=e.get("player", {}).get("name"),
                assist_id=e.get("assist", {}).get("id"),
                assist_name=e.get("assist", {}).get("name"),
                event_type=e.get("type"),
                event_detail=e.get("detail"),
                event_comments=e.get("comments"),
            ))
        return rows

    def _build_lineups(self, fixture_id, raw_lineups):
        rows = []
        for entry in raw_lineups:
            team = entry.get("team", {})
            formation = entry.get("formation")
            for p in entry.get("startXI", []):
                pl = p.get("player", {})
                rows.append(FixtureLineup(
                    fixture_id=fixture_id,
                    team_id=team["id"],
                    team_name=team["name"],
                    formation=formation,
                    player_id=pl["id"],
                    player_name=pl["name"],
                    player_number=self._safe_int(pl.get("number")),
                    position=pl.get("pos"),
                    grid=pl.get("grid"),
                    is_starter=True,
                ))
            for p in entry.get("substitutes", []):
                pl = p.get("player", {})
                rows.append(FixtureLineup(
                    fixture_id=fixture_id,
                    team_id=team["id"],
                    team_name=team["name"],
                    formation=formation,
                    player_id=pl["id"],
                    player_name=pl["name"],
                    player_number=self._safe_int(pl.get("number")),
                    position=pl.get("pos"),
                    grid=pl.get("grid"),
                    is_starter=False,
                ))
        return rows

    def _build_player_stats(self, fixture_id, raw_player_stats):
        rows = []
        for entry in raw_player_stats:
            player = entry.get("player", {})
            team_id = entry.get("team_id")
            stats_list = entry.get("statistics", [{}])
            s = stats_list[0] if stats_list else {}

            games = s.get("games", {})
            shots = s.get("shots", {})
            goals = s.get("goals", {})
            passes = s.get("passes", {})
            tackles = s.get("tackles", {})
            duels = s.get("duels", {})
            dribbles = s.get("dribbles", {})
            fouls = s.get("fouls", {})
            cards = s.get("cards", {})
            penalty = s.get("penalty", {})

            rows.append(FixturePlayerStats(
                fixture_id=fixture_id,
                team_id=team_id,
                player_id=player["id"],
                player_name=player["name"],
                minutes_played=self._safe_int(games.get("minutes")),
                rating=self._safe_decimal(games.get("rating")),
                captain=games.get("captain"),
                substitute=games.get("substitute"),
                goals=self._safe_int(goals.get("total")),
                assists=self._safe_int(goals.get("assists")),
                goals_conceded=self._safe_int(goals.get("conceded")),
                saves=self._safe_int(goals.get("saves")),
                shots_total=self._safe_int(shots.get("total")),
                shots_on_target=self._safe_int(shots.get("on")),
                passes_total=self._safe_int(passes.get("total")),
                passes_key=self._safe_int(passes.get("key")),
                passes_accuracy=self._safe_int(passes.get("accuracy")),
                tackles_total=self._safe_int(tackles.get("total")),
                tackles_blocks=self._safe_int(tackles.get("blocks")),
                tackles_interceptions=self._safe_int(tackles.get("interceptions")),
                duels_total=self._safe_int(duels.get("total")),
                duels_won=self._safe_int(duels.get("won")),
                dribbles_attempts=self._safe_int(dribbles.get("attempts")),
                dribbles_success=self._safe_int(dribbles.get("success")),
                fouls_committed=self._safe_int(fouls.get("committed")),
                fouls_drawn=self._safe_int(fouls.get("drawn")),
                yellow_cards=self._safe_int(cards.get("yellow")),
                red_cards=self._safe_int(cards.get("red")),
                penalty_scored=self._safe_int(penalty.get("scored")),
                penalty_missed=self._safe_int(penalty.get("missed")),
                penalty_saved=self._safe_int(penalty.get("saved")),
            ))
        return rows

    # ------------------------------------------------------------------ main job

    def persist_finished_fixtures(self):
        today = datetime.now(self.local_tz).strftime("%Y-%m-%d")
        local_time = datetime.now(self.local_tz).strftime("%H:%M:%S")
        print(f"PERSIST FINISHED 🚀 {local_time} - Persisting finished fixtures for {today}")

        if not self.r:
            print("PERSIST FINISHED 🚨 Redis unavailable, skipping.")
            return

        db = SessionLocal()
        try:
            match_service = MatchService(db)
            matches = match_service.get_matches_by_date(today)
            finished = [
                m for m in matches.get("data", [])
                if m.get("fixture", {}).get("status", {}).get("short") in FINISHED_STATUSES
            ]

            saved = 0
            skipped = 0

            for match in finished:
                fixture_id = match["fixture"]["id"]
                home_team_id = match["teams"]["home"]["id"]

                # skip if already persisted (idempotent)
                if db.query(Fixture).filter(Fixture.id == fixture_id).first():
                    skipped += 1
                    continue

                # read all data from Redis — no API calls here
                raw_stats = self.r.get(f"fixture_stats:{fixture_id}")
                raw_events = self.r.get(f"fixture_events:{fixture_id}")
                raw_lineups = self.r.get(f"fixture_lineups:{fixture_id}")
                raw_player_stats = self.r.get(f"fixture_player_stats:{fixture_id}")

                if not all([raw_stats, raw_events, raw_lineups, raw_player_stats]):
                    print(f"  -> ⚠️ Incomplete Redis data for fixture {fixture_id}, skipping.")
                    skipped += 1
                    continue

                db.add(self._build_fixture(match))

                for row in self._build_team_stats(fixture_id, home_team_id, json.loads(raw_stats)):
                    db.add(row)
                for row in self._build_events(fixture_id, json.loads(raw_events)):
                    db.add(row)
                for row in self._build_lineups(fixture_id, json.loads(raw_lineups)):
                    db.add(row)
                for row in self._build_player_stats(fixture_id, json.loads(raw_player_stats)):
                    db.add(row)

                db.commit()
                saved += 1
                print(f"  -> Persisted fixture {fixture_id}")

            self.notification_service.send_message(
                f"✅ Task Executed: Finished Fixtures Persisted ({saved} saved, {skipped} skipped)"
            )
            print(f"PERSIST FINISHED ✅ Done. {saved} saved, {skipped} skipped.")

        except Exception as e:
            db.rollback()
            print(f"PERSIST FINISHED 🚨 Error: {e}")
        finally:
            db.close()
