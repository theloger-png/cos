"""add users table

Revision ID: c3d1e2f4a5b6
Revises: baf8f13ea3b4
Create Date: 2026-06-09 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3d1e2f4a5b6'
down_revision: Union[str, Sequence[str], None] = 'baf8f13ea3b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'users',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('username', sa.String(length=255), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('hashed_password', sa.String(length=256), nullable=False),
        sa.Column('role', sa.String(length=32), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('username'),
        sa.UniqueConstraint('email'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('users')
