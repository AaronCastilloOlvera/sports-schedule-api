"""add bonus_stake to betting_tickets

Revision ID: d4e1b7c90a21
Revises: 760e417b7ade
Create Date: 2026-10-11 22:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e1b7c90a21'
down_revision: Union[str, Sequence[str], None] = '760e417b7ade'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('betting_tickets', sa.Column('bonus_stake', sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('betting_tickets', 'bonus_stake')
