"""bankroll_transactions.date DATE -> DATETIME

Revision ID: 971ee86c286f
Revises: c3ba88db153b
Create Date: 2026-09-20 00:00:00.000000

Written by hand — compare_type is off in migrations/env.py, so
`alembic revision --autogenerate` does not detect column type changes.

Postgres casts DATE -> TIMESTAMP by setting the time to midnight for every
existing row (documented, lossless assignment cast); no dates are dropped.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '971ee86c286f'
down_revision: Union[str, Sequence[str], None] = 'c3ba88db153b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        'bankroll_transactions', 'date',
        existing_type=sa.Date(),
        type_=sa.DateTime(),
        existing_nullable=False,
        postgresql_using='date::timestamp',
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Truncates any time-of-day captured after the upgrade back to the date —
    # inherent to reverting this column to DATE, not a separate data loss.
    op.alter_column(
        'bankroll_transactions', 'date',
        existing_type=sa.DateTime(),
        type_=sa.Date(),
        existing_nullable=False,
        postgresql_using='date::date',
    )
