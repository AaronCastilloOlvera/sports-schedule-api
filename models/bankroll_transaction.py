from sqlalchemy import Column, Integer, String, Float, DateTime
from .base import Base
import datetime

class BankrollTransaction(Base):
    __tablename__ = "bankroll_transactions"

    id = Column(Integer, primary_key=True, index=True)
    type = Column(String, nullable=False)  # 'deposit' or 'withdrawal'
    amount = Column(Float, nullable=False)
    date = Column(DateTime, nullable=False, default=datetime.datetime.now)
    notes = Column(String, nullable=True)
