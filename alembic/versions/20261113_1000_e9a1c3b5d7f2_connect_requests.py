"""Connect requests: the site's data posted by the plugin before the consent page

Revision ID: e9a1c3b5d7f2
Revises: d4f8b2c6a1e7
Create Date: 2026-11-13 10:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e9a1c3b5d7f2'
down_revision: str | None = 'd4f8b2c6a1e7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'connect_requests',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('site_host', sa.String(length=255), nullable=False),
        sa.Column('site_name', sa.String(length=200), nullable=False),
        sa.Column('site_url', sa.String(length=500), nullable=False),
        sa.Column('sender_email', sa.String(length=320), nullable=False),
        sa.Column('sender_name', sa.String(length=200), nullable=False),
        sa.Column('return_url', sa.String(length=500), nullable=False),
        sa.Column('state', sa.String(length=200), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_connect_requests_token_hash', 'connect_requests', ['token_hash'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_connect_requests_token_hash', table_name='connect_requests')
    op.drop_table('connect_requests')
