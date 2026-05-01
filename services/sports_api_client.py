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

  def get_team_recent_form(self, team_id: int, last: int = 10):
    url = f"{self.base_url}/fixtures"
    params = {"team": team_id, "last": last}
    try:
      response = requests.get(url, headers=self.headers, params=params)
      response.raise_for_status()
      return response.json().get("response", [])
    except requests.RequestException as e:
      print(f"Error fetching recent form for team {team_id}: {e}")
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