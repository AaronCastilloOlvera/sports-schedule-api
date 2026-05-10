from sqlalchemy import Column, Integer, String, Boolean, Numeric, ForeignKey
from .base import Base


class FixturePlayerStats(Base):
    __tablename__ = 'fixture_player_stats'

    id = Column(Integer, primary_key=True, autoincrement=True)
    fixture_id = Column(Integer, ForeignKey('fixtures.id'), nullable=False)
    team_id = Column(Integer, nullable=False)
    player_id = Column(Integer, nullable=False)
    player_name = Column(String, nullable=False)
    minutes_played = Column(Integer, nullable=True)
    rating = Column(Numeric(4, 2), nullable=True)
    captain = Column(Boolean, nullable=True)
    substitute = Column(Boolean, nullable=True)
    goals = Column(Integer, nullable=True)
    assists = Column(Integer, nullable=True)
    goals_conceded = Column(Integer, nullable=True)
    saves = Column(Integer, nullable=True)
    shots_total = Column(Integer, nullable=True)
    shots_on_target = Column(Integer, nullable=True)
    passes_total = Column(Integer, nullable=True)
    passes_key = Column(Integer, nullable=True)
    passes_accuracy = Column(Integer, nullable=True)
    tackles_total = Column(Integer, nullable=True)
    tackles_blocks = Column(Integer, nullable=True)
    tackles_interceptions = Column(Integer, nullable=True)
    duels_total = Column(Integer, nullable=True)
    duels_won = Column(Integer, nullable=True)
    dribbles_attempts = Column(Integer, nullable=True)
    dribbles_success = Column(Integer, nullable=True)
    fouls_committed = Column(Integer, nullable=True)
    fouls_drawn = Column(Integer, nullable=True)
    yellow_cards = Column(Integer, nullable=True)
    red_cards = Column(Integer, nullable=True)
    penalty_scored = Column(Integer, nullable=True)
    penalty_missed = Column(Integer, nullable=True)
    penalty_saved = Column(Integer, nullable=True)
