from fastapi import APIRouter, Query, Depends
from sqlalchemy.orm import Session
from dotenv import load_dotenv
from utils import database
from services.match_service import MatchService
from services.H2HService import H2HService

load_dotenv()

router = APIRouter(prefix="/matches", tags=["matches"])


@router.get("/by-date")
def get_matches_by_date(
    date: str = Query(..., description="Date in format YYYY-MM-DD"),
    db: Session = Depends(database.get_db),
):
    match_service = MatchService(db)
    return match_service.get_matches_by_date(date, force_refresh=False)


@router.get("/headtohead")
def get_headtohead_matches(
    team1: int = Query(..., description="ID of the first team"),
    team2: int = Query(..., description="ID of the second team"),
    db: Session = Depends(database.get_db),
):
    h2h_service = H2HService(db)
    return h2h_service.get_headtohead_matches(team1, team2)
