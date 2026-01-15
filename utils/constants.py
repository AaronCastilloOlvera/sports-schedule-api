"""
Shared constants for the application
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Headers for external API requests
HEADERS = {
    "x-rapidapi-host": os.getenv("API_URL"),
    "x-rapidapi-key": os.getenv("API_KEY")
}