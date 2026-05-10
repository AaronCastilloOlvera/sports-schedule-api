from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func
from .base import Base


class Fixture(Base):
    __tablename__ = 'fixtures'

    id = Column(Integer, primary_key=True)  # API-Sports fixture ID — no autoincrement
    league_id = Column(Integer, nullable=True)
    season = Column(Integer, nullable=True)
    date_utc = Column(DateTime(timezone=True), nullable=True)
    home_team_id = Column(Integer, nullable=False)
    home_team_name = Column(String, nullable=False)
    away_team_id = Column(Integer, nullable=False)
    away_team_name = Column(String, nullable=False)
    home_goals = Column(Integer, nullable=True)
    away_goals = Column(Integer, nullable=True)
    home_goals_ht = Column(Integer, nullable=True)
    away_goals_ht = Column(Integer, nullable=True)
    status = Column(String, nullable=False)  # FT | AET | PEN
    venue_name = Column(String, nullable=True)
    referee = Column(String, nullable=True)
    persisted_at = Column(DateTime(timezone=True), server_default=func.now())
