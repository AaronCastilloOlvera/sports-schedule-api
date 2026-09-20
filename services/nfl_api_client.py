import requests

_BASE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
_HEADERS = {"User-Agent": "Mozilla/5.0"}


class NFLApiClient:
    """
    Thin wrapper around ESPN's unofficial `site.api.espn.com` NFL endpoints.
    Free, no auth, no documented rate limit — but undocumented and unofficial,
    so callers must add `time.sleep(0.3)` between sequential calls (see
    `services/nfl_radar_service.py` and `tasks/prewarm_nfl_radar.py`) and never
    hammer it with calls that aren't needed.

    No business logic here — just HTTP + JSON passthrough, same philosophy as
    `services/mlb_api_client.py`.
    """

    def __init__(self):
        self.base = _BASE
        self.headers = _HEADERS

    def get_scoreboard(self, date: str | None = None, week: int | None = None,
                       seasontype: int = 2, year: int | None = None) -> dict:
        """
        date (YYYYMMDD)          -> that single day's games (any week).
        week + year (+seasontype) -> that week's ~14-16 games.
        Neither                  -> current week (ESPN's default).

        IMPORTANT / verified empirically: this endpoint does NOT accept a
        `year` query param to select a past season when combined with `week`.
        Passing `year=2025&week=5` silently ignores `year` and returns week 5
        of the CURRENT season instead. The season is actually selected via the
        `dates` param set to the bare year (`dates=2025&week=5`), which is
        confirmed to return the real 2025 week 5 slate with `completed: true`
        games. `dates` is overloaded by ESPN: `YYYYMMDD` for a single day,
        or `YYYY` for a season year when paired with `week`. This client
        hides that quirk — callers pass `year`, this method translates it to
        `dates` under the hood.
        """
        params: dict = {}
        if date:
            params["dates"] = date
        else:
            if year:
                params["dates"] = year
            if week:
                params["week"] = week
            params["seasontype"] = seasontype
        try:
            r = requests.get(f"{self.base}/scoreboard", headers=self.headers, params=params, timeout=15)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            print(f"[NFL CLIENT] scoreboard error (params={params}): {e}")
            return {}

    def get_summary(self, event_id) -> dict:
        """Boxscore + odds (`pickcenter`) for one event, pre or post game."""
        try:
            r = requests.get(f"{self.base}/summary", headers=self.headers,
                             params={"event": event_id}, timeout=15)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            print(f"[NFL CLIENT] summary error (event={event_id}): {e}")
            return {}
