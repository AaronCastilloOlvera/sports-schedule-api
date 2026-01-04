import google.genai as genai
import io
import json
import os
from fastapi import APIRouter, UploadFile, File
from PIL import Image
from google.genai import types
from dotenv import load_dotenv
from utils import database, schemas, crud
from sqlalchemy.orm import Session
from fastapi import Depends, Form, HTTPException
from datetime import date
from typing import Union, Optional
from datetime import timezone
from dateutil import parser
from services.bet_service import BetService

load_dotenv()

router = APIRouter(prefix="/bets", tags=["Bets"])
client = genai.Client(api_key=os.getenv("GENAI_API_KEY"))


@router.get("/", response_model=list[schemas.BettingTicket])
def read_betting_tickets(db: Session = Depends(database.get_db)):
  """
  Retrieve all betting tickets.
  """
  bet_service = BetService(db)

  return bet_service.get_tickets()

@router.get("/get-ticket-by-id")
def get_ticket_by_id(ticket_id: str, db: Session = Depends(database.get_db)):
  bet_service = BetService(db)
  return bet_service.get_ticket_by_id(ticket_id)

@router.post("/create-ticket")
async def create_betting_ticket(ticket: schemas.BettingTicketCreate, db: Session = Depends(database.get_db)):
  """
  Create a new betting ticket.
  """
  bet_service = BetService(db)
  return bet_service.create_ticket(ticket.dict())

@router.put("/update-ticket")
def update_betting_ticket(ticket: schemas.BettingTicketCreate, db: Session = Depends(database.get_db)):
  """
  Update a betting ticket by its ID.
  """
  bet_service = BetService(db)
  return bet_service.update_ticket(ticket.ticket_id, ticket.dict())

@router.delete("/delete-ticket")
def delete_betting_ticket(ticket_id: str, db: Session = Depends(database.get_db)):
  """
  Delete a betting ticket by its ID.
  """
  bet_service = BetService(db)
  return bet_service.delete_ticket(ticket_id)

@router.post("/upload-ticket-image")
async def upload_ticket_image(ticket_id: str, file: UploadFile = File(...), db: Session = Depends(database.get_db)):
  """
  Upload an image for a betting ticket by its ID.
  """
  bet_service = BetService(db)
  success = bet_service.update_ticket_image(ticket_id, file)
  if not success:
    return {"message": "Failed to upload image"}
  return {"message": "Image uploaded successfully"}

@router.post("/analyze-ticket")
async def analyze_betting_ticket(file: UploadFile = File(...)):
  """
  Analyze a betting ticket.

  Parameters:
  - **file**: The betting ticket data to be analyzed.
  """
  try:
    image_bytes = await file.read()
    img = Image.open(io.BytesIO(image_bytes))

    json_example = {
      "ticket_id": "ID_DEL_TICKET",
      "sport": "Futbol",
      "league": "Liga MX",
      "pick": "Over 2.5 goals",
      "odds": 1.95,
      "stake": 200.0,
      "payout": 350.0,
      "net_profit": 150.0,
      "status": "won",
      "match_name": "Club América vs Chivas",
      "isParley": False,
      "isCreateBet": False,
      "match_datetime": "2025-12-26T15:30:00",
      "device_type": "movil",
      "studied": True,
      "comments": ""
  }

    prompt = (
      "Analyze this betting ticket image and extract the data into JSON format. "
      "RULES: "
      "1. Convert American odds (+110, -150) to decimal (2.10, 1.66). "
      "2. Infer 'league' (e.g., Premier League, NBA) from the context. "
      "3. 'pick': Extract only what was chosen (e.g., 'Over 2.5', 'Both teams to score'). "
      "4. 'status': use 'pending' unless the ticket explicitly says 'won'/'paid' or 'lost'. "
      "5. 'net_profit': Calculate Payout minus Stake. "
      "6. 'match_name': Format as 'Team A vs Team B'. "
      "7. 'isParley': Set to true if there are 2 or more DIFFERENT matches. "
      "8. 'isCreateBet': Set to true if there are 2 or more selections for the SAME match. "
      "9. 'device_type': Detect if the layout is from a mobile app ('movil') or web browser ('desktop'). "
      "10. 'studied': always false. 'comments': always empty string. "
      "11. Format 'match_datetime' as ISO 8601. "
      "Return ONLY the JSON object, no markdown, no explanation. "
      f"Structure: {json.dumps(json_example)}"
    )
    
    response = client.models.generate_content(
      model="gemini-2.5-flash-lite",
      contents=[img, prompt],
      config=types.GenerateContentConfig(
          response_mime_type="application/json",
          temperature=0.1
      )
    )
    
    return json.loads(response.text)

  except Exception as e:
    return {"error": f"Failed to read file: {str(e)}"}

def convert_to_utc(date_str: str):    
    dt = parser.parse(date_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)