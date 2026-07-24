import requests

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
            "hydrate": "team,linescore,probablePitcher",
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
