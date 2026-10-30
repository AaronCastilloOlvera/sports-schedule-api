"""add placed_at to betting_tickets

Revision ID: e7a2c4f19b30
Revises: d4e1b7c90a21
Create Date: 2026-10-30 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7a2c4f19b30'
down_revision: Union[str, Sequence[str], None] = 'd4e1b7c90a21'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('betting_tickets', sa.Column('placed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('betting_tickets', 'placed_at')
