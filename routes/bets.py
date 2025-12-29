from fastapi import APIRouter, UploadFile, File
from PIL import Image
from google.genai import types
import google.genai as genai
import io
import json
import os
from dotenv import load_dotenv
from utils import database, schemas, crud
from sqlalchemy.orm import Session
from fastapi import Depends

load_dotenv()

router = APIRouter(prefix="/bets", tags=["Bets"])
client = genai.Client(api_key=os.getenv("GENAI_API_KEY"))

@router.post("/", response_model=schemas.BettingTicket)
def create_betting_ticket(ticket: schemas.BettingTicketCreate, db: Session = Depends(database.get_db)):
  """
  Create a new betting ticket.
  """
  return crud.create_betting_ticket(db=db, ticket=ticket)

@router.get("/", response_model=list[schemas.BettingTicket])
def read_betting_tickets(db: Session = Depends(database.get_db)):
  """
  Retrieve all betting tickets.
  """
  return crud.get_all_betting_tickets(db)



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

