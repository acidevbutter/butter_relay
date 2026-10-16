"""projects.delivery_secrets_enc: encrypted per-project delivery secrets (DVB-306)

Revision ID: f1a8c4d6b3e9
Revises: e7c3a5b8d2f6
Create Date: 2026-10-16 05:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'f1a8c4d6b3e9'
down_revision: str | None = 'e7c3a5b8d2f6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('projects', sa.Column('delivery_secrets_enc', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('projects', 'delivery_secrets_enc')
