"""make network cidr and gateway nullable (L2-only support)

Revision ID: d4e5f6a7b8c9
Revises: c3d1e2f4a5b6
Create Date: 2026-06-15 00:00:00.000000

Run `alembic upgrade head` on cos-controller after deploy.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c3d1e2f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('networks', 'cidr', existing_type=sa.String(length=18), nullable=True)
    op.alter_column('networks', 'gateway', existing_type=sa.String(length=45), nullable=True)


def downgrade() -> None:
    op.execute("UPDATE networks SET cidr = '' WHERE cidr IS NULL")
    op.execute("UPDATE networks SET gateway = '' WHERE gateway IS NULL")
    op.alter_column('networks', 'cidr', existing_type=sa.String(length=18), nullable=False)
    op.alter_column('networks', 'gateway', existing_type=sa.String(length=45), nullable=False)
