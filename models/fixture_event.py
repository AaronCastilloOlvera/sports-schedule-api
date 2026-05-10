from sqlalchemy import Column, Integer, String, ForeignKey
from .base import Base


class FixtureEvent(Base):
    __tablename__ = 'fixture_events'

    id = Column(Integer, primary_key=True, autoincrement=True)
    fixture_id = Column(Integer, ForeignKey('fixtures.id'), nullable=False)
    minute = Column(Integer, nullable=True)
    extra_minute = Column(Integer, nullable=True)
    team_id = Column(Integer, nullable=True)
    team_name = Column(String, nullable=True)
    player_id = Column(Integer, nullable=True)
    player_name = Column(String, nullable=True)
    assist_id = Column(Integer, nullable=True)
    assist_name = Column(String, nullable=True)
    event_type = Column(String, nullable=True)   # Goal | Card | subst | Var
    event_detail = Column(String, nullable=True)  # Normal Goal | Own Goal | Penalty | Yellow Card | ...
    event_comments = Column(String, nullable=True)
