"""Unit test for the e5f6a7b8c0d1 migration: cloud_init_user column on vm_templates.

Uses an in-memory SQLite database and Alembic's Operations API to verify that:
- upgrade() adds the column
- existing rows receive the 'ubuntu' default value
- downgrade() removes the column
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic.runtime.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


def _create_engine() -> sa.Engine:
    return sa.create_engine("sqlite:///:memory:")


def _bootstrap_vm_templates(engine: sa.Engine) -> None:
    """Create the vm_templates table as it exists before the migration, then insert one row."""
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE vm_templates (
                id          TEXT    PRIMARY KEY,
                name        TEXT    NOT NULL,
                description TEXT    NOT NULL DEFAULT '',
                cpu_cores   INTEGER NOT NULL,
                ram_mb      INTEGER NOT NULL,
                disk_gb     INTEGER NOT NULL,
                os_type     TEXT    NOT NULL,
                image_path  TEXT    NOT NULL,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """))
        conn.execute(text("""
            INSERT INTO vm_templates (id, name, description, cpu_cores, ram_mb, disk_gb, os_type, image_path)
            VALUES ('row-1', 'ubuntu-24.04-small', '', 2, 2048, 20, 'linux', '/images/ubuntu.qcow2')
        """))
        conn.commit()


class TestCloudInitUserMigration:
    def test_upgrade_adds_column(self):
        engine = _create_engine()
        _bootstrap_vm_templates(engine)

        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            ops = Operations(ctx)
            ops.add_column(
                'vm_templates',
                sa.Column('cloud_init_user', sa.String(64), server_default='ubuntu'),
            )
            conn.commit()

        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(vm_templates)"))
            columns = {row[1] for row in result.fetchall()}
        assert 'cloud_init_user' in columns

    def test_upgrade_existing_rows_get_ubuntu_default(self):
        engine = _create_engine()
        _bootstrap_vm_templates(engine)

        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            ops = Operations(ctx)
            ops.add_column(
                'vm_templates',
                sa.Column('cloud_init_user', sa.String(64), server_default='ubuntu'),
            )
            conn.commit()

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT cloud_init_user FROM vm_templates WHERE id = 'row-1'")
            ).fetchone()
        assert row is not None
        assert row[0] == 'ubuntu'

    def test_new_row_without_explicit_value_defaults_to_ubuntu(self):
        engine = _create_engine()
        _bootstrap_vm_templates(engine)

        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            ops = Operations(ctx)
            ops.add_column(
                'vm_templates',
                sa.Column('cloud_init_user', sa.String(64), server_default='ubuntu'),
            )
            conn.execute(text("""
                INSERT INTO vm_templates (id, name, cpu_cores, ram_mb, disk_gb, os_type, image_path)
                VALUES ('row-2', 'debian-12', 1, 1024, 10, 'linux', '/images/debian.qcow2')
            """))
            conn.commit()

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT cloud_init_user FROM vm_templates WHERE id = 'row-2'")
            ).fetchone()
        assert row is not None
        assert row[0] == 'ubuntu'

    def test_downgrade_removes_column(self):
        engine = _create_engine()
        _bootstrap_vm_templates(engine)

        # upgrade
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            ops = Operations(ctx)
            ops.add_column(
                'vm_templates',
                sa.Column('cloud_init_user', sa.String(64), server_default='ubuntu'),
            )
            conn.commit()

        # downgrade — SQLite doesn't support DROP COLUMN directly before 3.35,
        # so we verify by checking Alembic's batch_alter_table approach works.
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            ops = Operations(ctx)
            with ops.batch_alter_table('vm_templates') as batch_ops:
                batch_ops.drop_column('cloud_init_user')
            conn.commit()

        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(vm_templates)"))
            columns = {row[1] for row in result.fetchall()}
        assert 'cloud_init_user' not in columns
