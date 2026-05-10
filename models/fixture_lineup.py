from sqlalchemy import Column, Integer, String, Boolean, ForeignKey
from .base import Base


class FixtureLineup(Base):
    __tablename__ = 'fixture_lineups'

    id = Column(Integer, primary_key=True, autoincrement=True)
    fixture_id = Column(Integer, ForeignKey('fixtures.id'), nullable=False)
    team_id = Column(Integer, nullable=False)
    team_name = Column(String, nullable=False)
    formation = Column(String, nullable=True)
    player_id = Column(Integer, nullable=True)
    player_name = Column(String, nullable=True)
    player_number = Column(Integer, nullable=True)
    position = Column(String, nullable=True)  # G | D | M | F
    grid = Column(String, nullable=True)
    is_starter = Column(Boolean, nullable=False)
