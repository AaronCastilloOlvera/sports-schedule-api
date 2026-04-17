import os
import requests
from utils.constants import HEADERS

class SportsAPIClient:
  def __init__(self):
    self.base_url = f"https://{os.getenv('API_URL')}"
    self.headers = HEADERS
  
  def get_fixtures_by_date(self, date: str):
    """
    Fetch fixtures for a specific date from the external API.
    """

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