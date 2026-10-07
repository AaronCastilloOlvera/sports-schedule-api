import requests

_BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
_HEADERS = {"User-Agent": "Mozilla/5.0"}


class NBAApiClient:
    """
    Thin wrapper around ESPN's unofficial `site.api.espn.com` NBA endpoints.
    Free, no auth, undocumented -- callers add `time.sleep(0.3)` between
    sequential calls. Pure HTTP + JSON passthrough, same philosophy as
    `nfl_api_client.py`/`mlb_api_client.py` -- no business logic here.
    """

    def __init__(self):
        self.base = _BASE
        self.headers = _HEADERS

    def get_scoreboard(self, date: str | None = None, seasontype: int = 2,
                       year: int | None = None) -> dict:
        """
        date (YYYYMMDD) -> that day's games; omitted -> ESPN's current day.

        `seasontype`/`year` exist only for interface parity with
        `NFLApiClient` -- NOT sent as params. Unlike NFL, NBA has no
        `dates=YYYY`+`week` quirk; a plain `dates=YYYYMMDD` resolves any day.
        """
        params: dict = {}
        if date:
            params["dates"] = date
        try:
            r = requests.get(f"{self.base}/scoreboard", headers=self.headers, params=params, timeout=15)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            print(f"[NBA CLIENT] scoreboard error (params={params}): {e}")
            return {}

    def get_summary(self, event_id) -> dict:
        """Boxscore + odds (`pickcenter`) for one event, pre or post game."""
        try:
            r = requests.get(f"{self.base}/summary", headers=self.headers,
                             params={"event": event_id}, timeout=15)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            print(f"[NBA CLIENT] summary error (event={event_id}): {e}")
            return {}
