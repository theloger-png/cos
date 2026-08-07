"""drop nos_api_key from nodes

Revision ID: f6a7b8c0d1e2
Revises: e5f6a7b8c0d1
Create Date: 2026-08-07 00:00:00.000000

VLAN provisioning is now handled entirely by OVS + libvirt domain XML;
NOS integration has been fully removed from the codebase. The nos_api_key
column on nodes is no longer read or written by the controller.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f6a7b8c0d1e2'
down_revision: Union[str, Sequence[str], None] = 'e5f6a7b8c0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop nos_api_key from nodes."""
    op.drop_column('nodes', 'nos_api_key')


def downgrade() -> None:
    """Re-add nos_api_key to nodes."""
    op.add_column(
        'nodes',
        sa.Column('nos_api_key', sa.String(length=256), nullable=False, server_default=''),
    )
