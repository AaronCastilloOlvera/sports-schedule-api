import os
import requests

class NotificationService:
  def __init__(self):
    self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
    self.api_url = f"https://api.telegram.org/bot{self.bot_token}"

  def send_message(self, message: str):
    if not self.bot_token or not self.chat_id:
      print("Telegram bot token or chat ID not set. Skipping notification.")
      return
    
    payload = {
      "chat_id": self.chat_id,
      "text": message,
      "parse_mode": "Markdown"
    }

    try:
      response = requests.post(f"{self.api_url}/sendMessage", json=payload)
      response.raise_for_status()
    except requests.exceptions.RequestException as e:
      print(f"Failed to send Telegram message: {e}")