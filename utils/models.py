from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from .database import Base
import datetime

class League(Base):
    __tablename__ = 'leagues'

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    type = Column(String, index=True)
    logo = Column(String, index=True)
    is_favorite = Column(Boolean, default=False)

    country_id = Column(Integer, ForeignKey('countries.id'))
    country = relationship("Country", back_populates="leagues")

class Country(Base):
    __tablename__ = 'countries'

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String, unique=True, index=True)
    code = Column(String, index=True)
    flag = Column(String, index=True)

    leagues = relationship("League", back_populates="country")

class BettingTicket(Base):
    __tablename__ = "betting_tickets"

    id = Column(Integer, primary_key=True, index=True)
    ticket_id = Column(String, unique=True, index=True, nullable=False)
    sport = Column(String, default="Futbol", nullable=False)
    league = Column(String)
    pick = Column(String)
    odds = Column(Float)
    stake = Column(Float)
    payout = Column(Float)
    net_profit = Column(Float)
    status = Column(String, default="pending") # 'pending', 'won', 'lost'
    match_name = Column(String)
    bet_type = Column(String, nullable=True)
    match_datetime = Column(DateTime(timezone=True), nullable=False)
    device_type = Column(String)  # 'movil' or 'desktop'
    studied = Column(Boolean, default=False)
    comments = Column(String, default="")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    image_path = Column(String, nullable=True)
    