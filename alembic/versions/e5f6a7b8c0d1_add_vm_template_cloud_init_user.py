"""add cloud_init_user to vm_templates

Revision ID: e5f6a7b8c0d1
Revises: d4e5f6a7b8c9
Create Date: 2026-06-15 00:00:00.000000

Adds the cloud_init_user column so each template can specify which OS user
cloud-init should configure. Existing rows default to 'ubuntu'.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5f6a7b8c0d1'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add cloud_init_user with default 'ubuntu' to vm_templates."""
    op.add_column(
        'vm_templates',
        sa.Column(
            'cloud_init_user',
            sa.String(length=64),
            nullable=False,
            server_default='ubuntu',
        ),
    )


def downgrade() -> None:
    """Remove cloud_init_user from vm_templates."""
    op.drop_column('vm_templates', 'cloud_init_user')
