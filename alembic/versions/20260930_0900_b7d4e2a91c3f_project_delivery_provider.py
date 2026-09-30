"""per-project delivery provider (Project.delivery_provider/delivery_config, Message.provider)

Each company now chooses how its e-mail is delivered (Brevo by default, or SMTP)
instead of the provider being forced by the deployment environment.
Existing projects keep sending through Brevo.

Revision ID: b7d4e2a91c3f
Revises: fcf8813766b8
Create Date: 2026-09-30 09:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b7d4e2a91c3f'
down_revision: str | None = 'fcf8813766b8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'projects',
        sa.Column('delivery_provider', sa.String(length=20), server_default='brevo', nullable=False),
    )
    op.add_column(
        'projects',
        sa.Column('delivery_config', sa.JSON(), server_default='{}', nullable=False),
    )
    op.add_column('messages', sa.Column('provider', sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column('messages', 'provider')
    op.drop_column('projects', 'delivery_config')
    op.drop_column('projects', 'delivery_provider')
