"""api_keys.scopes (existing keys keep all) + projects.rate_per_minute (DVB-308 / S3)

Revision ID: b5d1f7a9c3e4
Revises: a3c9e5f7b1d2
Create Date: 2026-10-16 06:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b5d1f7a9c3e4'
down_revision: str | None = 'a3c9e5f7b1d2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing keys must keep working exactly as before: give them every scope.
    op.add_column(
        'api_keys',
        sa.Column(
            'scopes', sa.JSON(), nullable=False,
            server_default=sa.text("'[\"send\", \"read\", \"manage\"]'"),
        ),
    )
    op.add_column('projects', sa.Column('rate_per_minute', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('projects', 'rate_per_minute')
    op.drop_column('api_keys', 'scopes')
