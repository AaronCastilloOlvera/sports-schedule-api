from sqlalchemy import Column, Integer, String, Float, Date
from .base import Base
import datetime

class BankrollTransaction(Base):
    __tablename__ = "bankroll_transactions"

    id = Column(Integer, primary_key=True, index=True)
    type = Column(String, nullable=False)  # 'deposit' or 'withdrawal'
    amount = Column(Float, nullable=False)
    date = Column(Date, nullable=False, default=datetime.date.today)
    notes = Column(String, nullable=True)
