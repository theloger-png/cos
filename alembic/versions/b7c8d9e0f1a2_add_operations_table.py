"""add operations table

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-10-02 00:00:00.000000

Backs the portal's operations console: one row per state-changing action
(who, what, when, status, optional real progress, error).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'b7c8d9e0f1a2'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the operations table and its indexes."""
    op.create_table(
        'operations',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_label', sa.String(length=255), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('action', sa.String(length=64), nullable=False),
        sa.Column('description', sa.String(length=512), nullable=False),
        sa.Column('target_type', sa.String(length=32), nullable=True),
        sa.Column('target_id', sa.String(length=64), nullable=True),
        sa.Column('target_name', sa.String(length=255), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='running'),
        sa.Column('progress', sa.Integer(), nullable=True),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('http_method', sa.String(length=8), nullable=True),
        sa.Column('path', sa.String(length=512), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_operations_started_at', 'operations', ['started_at'])
    op.create_index('ix_operations_tenant_started', 'operations', ['tenant_id', 'started_at'])


def downgrade() -> None:
    """Drop the operations table."""
    op.drop_index('ix_operations_tenant_started', table_name='operations')
    op.drop_index('ix_operations_started_at', table_name='operations')
    op.drop_table('operations')
