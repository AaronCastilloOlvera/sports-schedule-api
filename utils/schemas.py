from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class LeagueBase(BaseModel):
    id: int
    name: str
    type: str
    logo: str
    country_id: str
    is_favorite: bool = False

class League(LeagueBase):
    id: int

    class Config:
        from_attributes = True

class CountryBase(BaseModel):
    name: str
    code: Optional[str] = None
    flag: Optional[str] = None

    class Config:
        from_attributes = True

class Country(CountryBase):
    id: int

    class Config:
        from_attributes = True

class LeagueResponse(BaseModel):
    league: League
    country: Country

    class Config:
        from_attributes = True

class LeagueOut(BaseModel):
    id: int
    name: str
    type: str
    logo: str
    is_favorite: bool
    country_id: int
    country: Optional[CountryBase] = None 

    class Config:
        from_attributes = True

class BettingTicketBase(BaseModel):
    ticket_id: str
    sport: str = "Futbol"
    league: str
    pick: str
    odds: float
    stake: float
    payout: float
    net_profit: float
    status: str = "pending"  # 'pending', 'won', 'lost'
    match_name: str
    bet_type: Optional[str] = None
    image_path: Optional[str] = None
    match_datetime: datetime
    device_type: str  # 'movil' or 'desktop'
    studied: bool = False
    comments: str = ""

class BettingTicketCreate(BettingTicketBase):
    pass

class BettingTicket(BettingTicketBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True