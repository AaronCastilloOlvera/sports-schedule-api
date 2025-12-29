"""rename bet_description to match_name

Revision ID: 3baf6cf52ab7
Revises: 3c8fe89330b4
Create Date: 2025-12-28 15:48:46.478759

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3baf6cf52ab7'
down_revision: Union[str, Sequence[str], None] = '3c8fe89330b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column('betting_tickets', 'bet_description', new_column_name='match_name')


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column('betting_tickets', 'match_name', new_column_name='bet_description')