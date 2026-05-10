from sqlalchemy import Column, Integer, String, Boolean, Numeric, ForeignKey
from .base import Base


class FixtureTeamStats(Base):
    __tablename__ = 'fixture_team_stats'

    id = Column(Integer, primary_key=True, autoincrement=True)
    fixture_id = Column(Integer, ForeignKey('fixtures.id'), nullable=False)
    team_id = Column(Integer, nullable=False)
    team_name = Column(String, nullable=False)
    is_home = Column(Boolean, nullable=False)
    shots_total = Column(Integer, nullable=True)
    shots_on_target = Column(Integer, nullable=True)
    shots_off_target = Column(Integer, nullable=True)
    shots_blocked = Column(Integer, nullable=True)
    shots_inside_box = Column(Integer, nullable=True)
    shots_outside_box = Column(Integer, nullable=True)
    possession = Column(Integer, nullable=True)
    passes_total = Column(Integer, nullable=True)
    passes_accurate = Column(Integer, nullable=True)
    passes_accuracy = Column(Integer, nullable=True)
    fouls = Column(Integer, nullable=True)
    corners = Column(Integer, nullable=True)
    offsides = Column(Integer, nullable=True)
    yellow_cards = Column(Integer, nullable=True)
    red_cards = Column(Integer, nullable=True)
    saves = Column(Integer, nullable=True)
    expected_goals = Column(Numeric(4, 2), nullable=True)
