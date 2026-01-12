from sqlalchemy import Column, Integer, String, Boolean
from .base import Base
from sqlalchemy.orm import relationship
from sqlalchemy import ForeignKey

class League(Base):
  __tablename__ = 'leagues'

  id = Column(Integer, primary_key=True, index=True)
  name = Column(String, index=True)
  type = Column(String, index=True)
  logo = Column(String, index=True)
  is_favorite = Column(Boolean, default=False)

  country_id = Column(Integer, ForeignKey('countries.id'))
  country = relationship("Country", back_populates="leagues")
