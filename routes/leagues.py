"""
Routes for League-related endpoints
"""
from typing import List, Optional
from fastapi import APIRouter, Query
import models
import database

router = APIRouter(prefix="/leagues", tags=["leagues"])

# Constants
FAVORITE_LEAGUES = [
    {"id": 2,   "name": "UEFA Champions League", "country": "World"},
    {"id": 39,  "name": "Premier League",        "country": "England"},
    {"id": 40,  "name": "EFL Championship",      "country": "England"},
    {"id": 61,  "name": "Ligue 1",               "country": "France"},
    {"id": 78,  "name": "Bundesliga",            "country": "Germany"},
    {"id": 135, "name": "Serie A",               "country": "Italy"},
    {"id": 140, "name": "La Liga",               "country": "Spain"},
    {"id": 262, "name": "Liga MX",               "country": "Mexico"}
]

@router.get("")
def get_leagues(id: Optional[List[int]] = Query(None)):
    """
    Get all leagues or filter by specific IDs
    """
    db = database.SessionLocal()
    try:
        query = db.query(models.League).order_by(models.League.id)
        if id:
            query = query.filter(models.League.id.in_(id))
        leagues = query.all()
        return leagues
    except Exception as e:
        return {"error": str(e)}
    finally:
        db.close()


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
