import json
from services.sports_api_client import SportsAPIClient
from utils.redis_client import get_redis_connection

class H2HService:
  def __init__(self):
    self.api_client = SportsAPIClient()
    self.r, _ = get_redis_connection()
  
  def get_headtohead_matches(self, team1: int, team2: int):
    """
    Get head-to-head matches between two teams, with Redis caching.
    """
    ids = sorted([team1, team2])
    cache_key = f"h2h:teams:{ids[0]}&{ids[1]}"

    if self.r: 
      cached_data = self.r.get(cache_key) # Fetch from Redis cache
      if cached_data:
        return json.loads(cached_data)
      
    # Fetch from external API if not in cache
    h2h_data = self.api_client.get_headtohead_matches(ids[0], ids[1])

    if self.r and h2h_data:
      self.r.setex(cache_key, 864000, json.dumps(h2h_data)) # Cache for 10 days
  
    return { "data": h2h_data}