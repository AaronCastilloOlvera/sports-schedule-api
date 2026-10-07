"""
Thin HTTP client for ESPN's unofficial MLB endpoints. Replaces the old
`statsapi.mlb.com` client -- pure HTTP + JSON passthrough, same philosophy as
`nfl_api_client.py`/`nba_api_client.py`. All parsing lives in
`mlb_radar_service.py`/`baseball_service.py`.

Two hosts: site.api.espn.com (scoreboard/summary), site.web.api.espn.com
(pitcher gamelog). MLB-only -- ESPN has no LMB coverage (both `.../lmb` and
`.../mexican-league` return 400).
"""
import requests

_BASE = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb"
_GAMELOG_BASE = "https://site.web.api.espn.com/apis/common/v3/sports/baseball/mlb"
_HEADERS = {"User-Agent": "Mozilla/5.0"}

# Static team -> IANA timezone table, all 30 MLB teams. ESPN's venue payload
# has no timezone/coordinates, and 30 stadiums rarely change -- a static
# lookup replaces the old per-venue API call + its Redis cache entirely.
TEAM_TIMEZONES = {
    # AL East
    'NYY': 'America/New_York',     # Yankee Stadium, Bronx, NY
    'BOS': 'America/New_York',     # Fenway Park, Boston, MA
    'TB':  'America/New_York',     # Tropicana Field / Steinbrenner Field, Tampa/St. Petersburg, FL
    'TOR': 'America/Toronto',      # Rogers Centre, Toronto, ON
    'BAL': 'America/New_York',     # Camden Yards, Baltimore, MD
    # AL Central
    'CHW': 'America/Chicago',      # Guaranteed Rate Field, Chicago, IL
    'CLE': 'America/New_York',     # Progressive Field, Cleveland, OH (Eastern)
    'DET': 'America/Detroit',      # Comerica Park, Detroit, MI (Eastern)
    'KC':  'America/Chicago',      # Kauffman Stadium, Kansas City, MO (Central)
    'MIN': 'America/Chicago',      # Target Field, Minneapolis, MN
    # AL West
    'HOU': 'America/Chicago',      # Minute Maid Park, Houston, TX (Central, not Mountain)
    'LAA': 'America/Los_Angeles',  # Angel Stadium, Anaheim, CA
    'ATH': 'America/Los_Angeles',  # Sutter Health Park, West Sacramento, CA (Athletics, 2025+)
    'OAK': 'America/Los_Angeles',  # Alias for the Athletics under their old abbreviation
    'SEA': 'America/Los_Angeles',  # T-Mobile Park, Seattle, WA
    'TEX': 'America/Chicago',      # Globe Life Field, Arlington, TX (Central)
    # NL East
    'ATL': 'America/New_York',     # Truist Park, Atlanta, GA
    'MIA': 'America/New_York',     # loanDepot Park, Miami, FL
    'NYM': 'America/New_York',     # Citi Field, Queens, NY
    'PHI': 'America/New_York',     # Citizens Bank Park, Philadelphia, PA
    'WSH': 'America/New_York',     # Nationals Park, Washington, DC
    # NL Central
    'CHC': 'America/Chicago',      # Wrigley Field, Chicago, IL
    'CIN': 'America/New_York',     # Great American Ball Park, Cincinnati, OH (Eastern)
    'MIL': 'America/Chicago',      # American Family Field, Milwaukee, WI
    'PIT': 'America/New_York',     # PNC Park, Pittsburgh, PA (Eastern)
    'STL': 'America/Chicago',      # Busch Stadium, St. Louis, MO (Central)
    # NL West
    'ARI': 'America/Phoenix',      # Chase Field, Phoenix, AZ (no DST, NOT America/Denver)
    'COL': 'America/Denver',       # Coors Field, Denver, CO
    'LAD': 'America/Los_Angeles',  # Dodger Stadium, Los Angeles, CA
    'SD':  'America/Los_Angeles',  # Petco Park, San Diego, CA
    'SF':  'America/Los_Angeles',  # Oracle Park, San Francisco, CA
}


class MLBApiClient:
    def __init__(self):
        self.base = _BASE
        self.gamelog_base = _GAMELOG_BASE
        self.headers = _HEADERS

    def get_scoreboard(self, date: str) -> dict:
        """Raw ESPN scoreboard response for one day. `date` is YYYYMMDD."""
        try:
            r = requests.get(f"{self.base}/scoreboard", headers=self.headers,
                             params={"dates": date}, timeout=15)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            print(f"[MLB CLIENT] scoreboard error (date={date}): {e}")
            return {}

    def get_summary(self, event_id) -> dict:
        """Raw ESPN summary for one event -- boxscore, linescores (with
        per-inning hits), and `pickcenter` odds (DraftKings when available)."""
        try:
            r = requests.get(f"{self.base}/summary", headers=self.headers,
                             params={"event": event_id}, timeout=15)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            print(f"[MLB CLIENT] summary error (event={event_id}): {e}")
            return {}

    def get_pitcher_gamelog(self, athlete_id, season: int) -> dict:
        """
        Raw per-start pitching log for one season. Different host
        (`site.web.api.espn.com`). `stats` arrays are POSITIONAL against
        `labels`; per-event metadata lives in a separate top-level `events`
        dict keyed by eventId -- see `build_espn_pitcher_splits()` for the join.
        """
        try:
            r = requests.get(
                f"{self.gamelog_base}/athletes/{athlete_id}/gamelog",
                headers=self.headers,
                params={"season": season, "category": "pitching"},
                timeout=15,
            )
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            print(f"[MLB CLIENT] pitcher gamelog error (id={athlete_id}, season={season}): {e}")
            return {}

    @staticmethod
    def get_team_timezone(team_abbr: str | None) -> str | None:
        """Pure lookup, no HTTP call, no cache needed -- see TEAM_TIMEZONES."""
        if not team_abbr:
            return None
        return TEAM_TIMEZONES.get(team_abbr.upper())
