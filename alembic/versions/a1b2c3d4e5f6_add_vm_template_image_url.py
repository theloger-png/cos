"""add image_url to vm_templates

Revision ID: a1b2c3d4e5f6
Revises: f6a7b8c0d1e2
Create Date: 2026-10-01 00:00:00.000000

Adds the nullable image_url column so a template's base image can be
downloaded from a URL and distributed to every node automatically, instead
of requiring image_path to be a pre-existing local file an operator placed
there by hand. image_path remains the authoritative local path used at VM
creation time (unchanged); image_url is the source used to (re)populate it
via POST /api/v1/templates/{id}/fetch-image.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'f6a7b8c0d1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add nullable image_url to vm_templates."""
    op.add_column(
        'vm_templates',
        sa.Column('image_url', sa.String(length=1024), nullable=True),
    )


def downgrade() -> None:
    """Remove image_url from vm_templates."""
    op.drop_column('vm_templates', 'image_url')
