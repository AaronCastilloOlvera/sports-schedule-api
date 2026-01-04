from pydantic import BaseModel
from datetime import datetime
from typing import Optional

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
        orm_mode = True