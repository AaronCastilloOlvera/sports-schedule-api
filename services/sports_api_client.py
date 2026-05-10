import os
import requests
from utils.constants import HEADERS

class SportsAPIClient:
  def __init__(self):
    self.base_url = f"https://{os.getenv('API_URL')}"
    self.headers = HEADERS

  def get_fixtures_by_date(self, date: str):
    """Fetch fixtures for a specific date from the external API."""
    url = f"{self.base_url}/fixtures"
    params = {"date": date, "timezone": "America/Mexico_City"}
    try:
      print(f"Fetching fixtures for date {date} from API...")
      response = requests.get(url, headers=self.headers, params=params)
      response.raise_for_status()
      return response.json().get("response", [])
    except requests.RequestException as e:
      print(f"Error fetching fixtures for date {date}: {e}")
      return []

  def get_team_last_matches(self, team_id: int, last: int = 10):
    url = f"{self.base_url}/fixtures"
    params = {"team": team_id, "last": last}
    try:
      response = requests.get(url, headers=self.headers, params=params)
      response.raise_for_status()
      return response.json().get("response", [])
    except requests.RequestException as e:
      print(f"Error fetching last matches for team {team_id}: {e}")
      return []

  def get_fixture_statistics(self, fixture_id: int):
    url = f"{self.base_url}/fixtures/statistics"
    params = {"fixture": fixture_id}
    try:
      response = requests.get(url, headers=self.headers, params=params)
      response.raise_for_status()
      return response.json().get("response", [])
    except requests.RequestException as e:
      print(f"Error fetching statistics for fixture {fixture_id}: {e}")
      return []

  def get_fixture_events(self, fixture_id: int):
    """Fetch the complete event list for a single fixture."""
    url = f"{self.base_url}/fixtures/events"
    params = {"fixture": fixture_id}
    try:
      response = requests.get(url, headers=self.headers, params=params)
      response.raise_for_status()
      return response.json().get("response", [])
    except requests.RequestException as e:
      print(f"Error fetching events for fixture {fixture_id}: {e}")
      return []

  def get_live_fixtures(self):
    """Fetch all currently live fixtures. Response includes events and statistics."""
    url = f"{self.base_url}/fixtures"
    params = {"live": "all"}
    try:
      response = requests.get(url, headers=self.headers, params=params)
      response.raise_for_status()
      return response.json().get("response", [])
    except requests.RequestException as e:
      print(f"Error fetching live fixtures: {e}")
      return []

  def get_headtohead_matches(self, team1: int, team2: int):
    url = f"{self.base_url}/fixtures/headtohead"
    params = {"h2h": f"{team1}-{team2}"}
    try:
      response = requests.get(url, headers=self.headers, params=params)
      response.raise_for_status()
      return response.json().get("response", [])
    except requests.RequestException as e:
      print(f"Error fetching head-to-head matches for teams {team1} and {team2}: {e}")
      return []

  def get_fixture_lineups(self, fixture_id: int):
    url = f"{self.base_url}/fixtures/lineups"
    params = {"fixture": fixture_id}
    try:
      response = requests.get(url, headers=self.headers, params=params)
      response.raise_for_status()
      return response.json().get("response", [])
    except requests.RequestException as e:
      print(f"Error fetching lineups for fixture {fixture_id}: {e}")
      return []

  def get_fixture_player_statistics(self, fixture_id: int):
    url = f"{self.base_url}/fixtures/players"
    params = {"fixture": fixture_id}
    try:
      response = requests.get(url, headers=self.headers, params=params)
      response.raise_for_status()
      return response.json().get("response", [])
    except requests.RequestException as e:
      print(f"Error fetching player stats for fixture {fixture_id}: {e}")
      return []

  def get_odds_by_fixture(self, fixture_id: int):
    """Fetch betting odds for a specific fixture."""
    url = f"{self.base_url}/odds"
    params = {"fixture": fixture_id}
    try:
      response = requests.get(url, headers=self.headers, params=params)
      response.raise_for_status()
      return response.json().get("response", [])
    except requests.RequestException as e:
      print(f"Error fetching odds for fixture {fixture_id}: {e}")
      return []