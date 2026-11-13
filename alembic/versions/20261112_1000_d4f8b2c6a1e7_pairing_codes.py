"""Pairing codes: connect a WordPress plugin to a project with a one-time code

Revision ID: d4f8b2c6a1e7
Revises: c7e3a9b5d1f4
Create Date: 2026-11-12 10:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd4f8b2c6a1e7'
down_revision: str | None = 'c7e3a9b5d1f4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'pairing_codes',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('code_hash', sa.String(length=64), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('label', sa.String(length=200), nullable=False),
        sa.Column('site_host', sa.String(length=255), nullable=False),
        sa.Column('approved_by', sa.String(length=320), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_pairing_codes_code_hash', 'pairing_codes', ['code_hash'], unique=True)
    op.create_index('ix_pairing_codes_project_id', 'pairing_codes', ['project_id'])


def downgrade() -> None:
    op.drop_index('ix_pairing_codes_project_id', table_name='pairing_codes')
    op.drop_index('ix_pairing_codes_code_hash', table_name='pairing_codes')
    op.drop_table('pairing_codes')
