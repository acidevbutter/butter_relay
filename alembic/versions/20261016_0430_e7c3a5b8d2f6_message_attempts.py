"""message_attempts: history of delivery attempts per message (DVB-188)

Revision ID: e7c3a5b8d2f6
Revises: d2b9e6a4f1c8
Create Date: 2026-10-16 04:30:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e7c3a5b8d2f6'
down_revision: str | None = 'd2b9e6a4f1c8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'message_attempts',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('message_id', sa.Uuid(), nullable=False),
        sa.Column('number', sa.Integer(), nullable=False),
        sa.Column('outcome', sa.String(length=20), nullable=False),
        sa.Column('terminal', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('provider_message_id', sa.String(length=200), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_message_attempts_message_id'), 'message_attempts', ['message_id'], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_message_attempts_message_id'), table_name='message_attempts')
    op.drop_table('message_attempts')
