from sqlalchemy import Column, Integer, String
from database import Base

class League(Base):
    __tablename__ = 'leagues'

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    type = Column(String, index=True)
    logo = Column(String, index=True)
    country_id = Column(String, index=True)
