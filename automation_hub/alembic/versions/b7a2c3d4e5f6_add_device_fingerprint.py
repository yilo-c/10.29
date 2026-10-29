"""add_device_fingerprint

Revision ID: b7a2c3d4e5f6
Revises: 1f9cc4ea9500
Create Date: 2026-05-02 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7a2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '1f9cc4ea9500'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add fingerprint column to devices table for device identity."""
    op.add_column('devices', sa.Column('fingerprint', sa.Text(), nullable=True,
        comment='SHA-256 hash of device fingerprint for identity verification'))


def downgrade() -> None:
    """Remove fingerprint column."""
    op.drop_column('devices', 'fingerprint')
