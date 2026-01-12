from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship
from .base import Base

class Country(Base):
  __tablename__ = 'countries'

  id = Column(Integer, primary_key=True, index=True, autoincrement=True)
  name = Column(String, unique=True, index=True)
  code = Column(String, index=True)
  flag = Column(String, index=True)

  leagues = relationship("League", back_populates="country")
