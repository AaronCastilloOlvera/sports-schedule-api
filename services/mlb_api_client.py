import requests
from datetime import datetime

_BASE = "https://statsapi.mlb.com/api/v1"
_HEADERS = {"User-Agent": "Mozilla/5.0"}

LEAGUES = {
    "lmb": {"sportId": 23, "leagueId": 125},
    "mlb": {"sportId": 1,  "leagueId": None},
}


class MLBApiClient:
    def __init__(self):
        self.base = _BASE
        self.headers = _HEADERS

    def get_schedule(self, date: str, league: str = "lmb") -> list:
        cfg = LEAGUES.get(league, LEAGUES["lmb"])
        params = {
            "sportId": cfg["sportId"],
            "date": date,
            # team(leagueRecord) → win/loss record shown next to each team name
            "hydrate": "team(leagueRecord),linescore,probablePitcher",
        }
        if cfg["leagueId"]:
            params["leagueId"] = cfg["leagueId"]
        try:
            r = requests.get(f"{self.base}/schedule", headers=self.headers, params=params, timeout=10)
            r.raise_for_status()
            dates = r.json().get("dates", [])
            return dates[0]["games"] if dates else []
        except requests.RequestException as e:
            print(f"[MLB CLIENT] schedule error ({league} {date}): {e}")
            return []

    def get_boxscore(self, game_pk: int) -> dict:
        try:
            r = requests.get(f"{self.base}/game/{game_pk}/boxscore", headers=self.headers, timeout=10)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            print(f"[MLB CLIENT] boxscore error (gamePk={game_pk}): {e}")
            return {}

    def get_person_stats(self, person_id: int, league: str = "lmb", season: int | None = None) -> dict:
        # sportId matters here — LMB stats are invisible without it (defaults to
        # MLB-only otherwise, returning an empty split for LMB-only players).
        cfg = LEAGUES.get(league, LEAGUES["lmb"])
        params = {
            "stats": "season",
            "group": "pitching",
            "sportId": cfg["sportId"],
            "season": season or datetime.now().year,
        }
        stat = {}
        try:
            r = requests.get(f"{self.base}/people/{person_id}/stats", headers=self.headers, params=params, timeout=10)
            r.raise_for_status()
            stats_list = r.json().get("stats", [])
            splits = stats_list[0].get("splits", []) if stats_list else []
            stat = splits[0]["stat"] if splits else {}
        except requests.RequestException as e:
            print(f"[MLB CLIENT] pitcher stats error (id={person_id}): {e}")

        # Merge in pitchHand (RHP/LHP) — doesn't change, but there's no combined
        # endpoint, so fold it into this same cached response instead of a
        # second round-trip from the frontend.
        try:
            r = requests.get(f"{self.base}/people/{person_id}", headers=self.headers, timeout=10)
            r.raise_for_status()
            people = r.json().get("people", [])
            stat["pitchHand"] = people[0].get("pitchHand") if people else None
        except requests.RequestException as e:
            print(f"[MLB CLIENT] person bio error (id={person_id}): {e}")

        return stat

    def get_person_game_log(self, person_id: int, league: str = "lmb", seasons: list[int] | None = None) -> list:
        # Same sportId semantics as get_person_stats — one row per start, each
        # carrying its own `opponent` team so the caller can filter by matchup.
        # gameLog is inherently single-season on this API (no "career" mode),
        # so multi-season history means one call per year, concatenated here.
        cfg = LEAGUES.get(league, LEAGUES["lmb"])
        years = seasons or [datetime.now().year]
        all_splits = []
        for year in years:
            params = {
                "stats": "gameLog",
                "group": "pitching",
                "sportId": cfg["sportId"],
                "season": year,
            }
            try:
                r = requests.get(f"{self.base}/people/{person_id}/stats", headers=self.headers, params=params, timeout=10)
                r.raise_for_status()
                stats_list = r.json().get("stats", [])
                splits = stats_list[0].get("splits", []) if stats_list else []
                all_splits.extend(splits)
            except requests.RequestException as e:
                print(f"[MLB CLIENT] game log error (id={person_id}, season={year}): {e}")
        return all_splits

    def get_game_final_score(self, game_pk: int) -> dict | None:
        # gameLog splits carry the pitcher's own stats but never the team's
        # final score — /schedule keyed by a single gamePk does.
        try:
            r = requests.get(f"{self.base}/schedule", headers=self.headers, params={"gamePk": game_pk}, timeout=10)
            r.raise_for_status()
            dates = r.json().get("dates", [])
            games = dates[0]["games"] if dates else []
            game = games[0] if games else None
            if not game:
                return None
            teams = game.get("teams", {})
            return {
                "home": teams.get("home", {}).get("score"),
                "away": teams.get("away", {}).get("score"),
                "homeTeamId": teams.get("home", {}).get("team", {}).get("id"),
                "awayTeamId": teams.get("away", {}).get("team", {}).get("id"),
            }
        except requests.RequestException as e:
            print(f"[MLB CLIENT] final score error (gamePk={game_pk}): {e}")
            return None
