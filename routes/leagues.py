"""
Routes for League-related endpoints
"""
import os
import models
import requests
from utils.constants import FAVORITE_LEAGUES, HEADERS
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional
from fastapi import APIRouter, Query, Depends, HTTPException
from utils import database
from sqlalchemy.orm import Session
from utils.schemas import LeagueOut

router = APIRouter(prefix="/leagues", tags=["leagues"])

@router.get("", response_model=List[LeagueOut])
def get_leagues(id: Optional[List[int]] = Query(None)):
  """
  Get all leagues or filter by specific IDs
  """
  db = database.SessionLocal()
  try:
      query = db.query(models.League).options(joinedload(models.League.country)).order_by(models.League.id)
      
      if id:
          query = query.filter(models.League.id.in_(id))
      leagues = query.all()
      return leagues
  except Exception as e:
      return {"error": str(e)}
  finally:
      db.close()

@router.get("get-league-by-id", response_model=LeagueOut)
def get_league_by_id(league_id: int, db: Session = Depends(database.get_db)):
  """
  Get a league by its ID
  """
  league = db.query(models.League).options(joinedload(models.League.country)).filter(models.League.id == league_id).first()
  if not league:
      raise HTTPException(status_code=404, detail="League not found")
  return league

@router.put("update-league")
def update_league(league_id: int, is_favorite: bool, db: Session = Depends(database.get_db)):
  """
  Update the favorite status of a league
  """
  league = db.query(models.League).filter(models.League.id == league_id).first()
  if not league:
      raise HTTPException(status_code=404, detail="League not found")
  
  league.is_favorite = is_favorite
  db.commit()
  db.refresh(league)
  return league

@router.get("/favorite")
def get_favorite_leagues():
  """
  Get all favorite leagues
  """
  db = database.SessionLocal()
  try:
      query = db.query(models.League).filter(
          models.League.id.in_(FAVORITE_LEAGUES_IDs := [league["id"] for league in FAVORITE_LEAGUES])
      ).order_by(models.League.id)
      return query.all()
  except Exception as e:
      return {"error": str(e)}
  finally:
      db.close()



@router.get("/sync-api-leagues")
def sync_api_leagues(db: Session = Depends(database.get_db)):
  
  url = f"https://{os.getenv('API_URL')}/leagues?season=2025"  

  try:
    response = requests.get(url, headers=HEADERS)
    response.raise_for_status() 
    data = response.json()

    league_list = data.get("response", [])

    if not league_list:
      raise HTTPException(status_code=404, detail="No leagues found in API response")
    
    total_saved = save_league_to_db(db, league_list)

    return {"message": f"Successfully saved {total_saved} leagues to database"}

  except requests.RequestException as e:
    db.rollback()
    raise HTTPException(status_code=500, detail=f"API request error: {str(e)}")

def save_league_to_db(db: Session, api_response: list):
    for item in api_response:
        country_data = item.get("country")
        league_data = item.get("league")
        
        country_name = country_data.get("name")
        
        db_country = db.query(models.Country).filter(models.Country.name == country_name).first()

        if db_country:
            db_country.code = country_data.get("code")
            db_country.flag = country_data.get("flag")
        else:
            db_country = models.Country(
                name=country_name,
                code=country_data.get("code"),
                flag=country_data.get("flag")
            )
            db.add(db_country)
            db.flush() 

        db_league = models.League(
            id=league_data.get("id"),
            name=league_data.get("name"),
            type=league_data.get("type"),
            logo=league_data.get("logo"),
            country_id=db_country.id, 
            is_favorite=False
        )
        db.merge(db_league)

    db.commit()
    return len(api_response)