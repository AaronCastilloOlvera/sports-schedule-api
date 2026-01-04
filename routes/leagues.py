"""
Routes for League-related endpoints
"""
from typing import List, Optional
from fastapi import APIRouter, Query
from utils import database
import utils.models as models

router = APIRouter(prefix="/leagues", tags=["leagues"])

# Constants
FAVORITE_LEAGUES = [
    # --- CONTINENTALES ---
    {"id": 2,   "name": "UEFA Champions League",     "country": "World",   "emoji": "🌍"},
    {"id": 3,   "name": "UEFA Europa League",        "country": "World",   "emoji": "🌍"},
    {"id": 848, "name": "UEFA Conference League",    "country": "World",   "emoji": "🌍"},
    {"id": 13,  "name": "Copa Libertadores",         "country": "World",   "emoji": "🏆"},
    {"id": 11,  "name": "Copa Sudamericana",         "country": "World",   "emoji": "🏆"},

    # --- INGLATERRA ---
    {"id": 39,  "name": "Premier League",            "country": "England", "emoji": "🏴󠁧󠁢󠁥󠁮󠁧󠁿"},
    {"id": 40,  "name": "EFL Championship",          "country": "England", "emoji": "🏴󠁧󠁢󠁥󠁮󠁧󠁿"},
    {"id": 45,  "name": "FA Cup",                    "country": "England", "emoji": "🏴󠁧󠁢󠁥󠁮󠁧󠁿"},

    # --- ESPAÑA ---
    {"id": 140, "name": "La Liga",                   "country": "Spain",   "emoji": "🇪🇸"},
    {"id": 143, "name": "Copa del Rey",              "country": "Spain",   "emoji": "🇪🇸"},

    # --- ITALIA ---
    {"id": 135, "name": "Serie A",                   "country": "Italy",   "emoji": "🇮🇹"},
    {"id": 137, "name": "Coppa Italia",              "country": "Italy",   "emoji": "🇮🇹"},
    {"id": 547, "name": "Supercoppa Italiana",       "country": "Italy",   "emoji": "🇮🇹"},

    # --- ALEMANIA ---
    {"id": 78,  "name": "Bundesliga",                "country": "Germany", "emoji": "🇩🇪"},
    {"id": 79,  "name": "2. Bundesliga",             "country": "Germany", "emoji": "🇩🇪"},
    {"id": 81,  "name": "DFB Pokal",                 "country": "Germany", "emoji": "🇩🇪"},

    # --- FRANCIA ---
    {"id": 61,  "name": "Ligue 1",                   "country": "France",  "emoji": "🇫🇷"},
    {"id": 66,  "name": "Coupe de France",           "country": "France",  "emoji": "🇫🇷"},

    # --- MÉXICO ---
    {"id": 262, "name": "Liga MX",                   "country": "Mexico",  "emoji": "🇲🇽"},
    {"id": 264, "name": "Liga MX Femenil",           "country": "Mexico",  "emoji": "🇲🇽"},
    {"id": 263, "name": "Liga de Expansión MX",      "country": "Mexico",  "emoji": "🇲🇽"},

    # --- RESTO DE AMÉRICA ---
    {"id": 253, "name": "MLS",                       "country": "USA",     "emoji": "🇺🇸"},
    {"id": 71,  "name": "Série A",                   "country": "Brazil",  "emoji": "🇧🇷"},
    {"id": 73,  "name": "Copa Do Brasil",            "country": "Brazil",  "emoji": "🇧🇷"},
    {"id": 128, "name": "Liga Profesional",          "country": "Argentina", "emoji": "🇦🇷"},
    {"id": 34,  "name": "WC Qualification South America", "country": "World", "emoji": "🌎"},

    # --- EUROPA (TALENTO & MERCADO) ---
    {"id": 88,  "name": "Eredivisie",                "country": "Netherlands", "emoji": "🇳🇱"},
    {"id": 94,  "name": "Primeira Liga",             "country": "Portugal",    "emoji": "🇵🇹"},
    {"id": 203, "name": "Süper Lig",                 "country": "Turkey",      "emoji": "🇹🇷"},
    {"id": 179, "name": "Scottish Premiership",      "country": "Scotland",    "emoji": "🏴󠁧󠁢󠁳󠁣󠁴󠁿"},

    # --- ASIA, OCEANÍA & OTROS ---
    {"id": 307, "name": "Saudi Pro League",          "country": "Saudi Arabia", "emoji": "🇸🇦"}
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
