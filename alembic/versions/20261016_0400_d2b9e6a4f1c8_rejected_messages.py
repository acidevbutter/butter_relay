"""rejected_messages: tasks the worker could not parse (DVB-456)

Malformed tasks (project id that is not a UUID, missing template key or recipient)
used to be logged and dropped while Celery reported the task as succeeded. They now
get a row here; `messages` cannot hold them because it requires a real Project.

Revision ID: d2b9e6a4f1c8
Revises: c5a8d1f3e7b2
Create Date: 2026-10-16 04:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd2b9e6a4f1c8'
down_revision: str | None = 'c5a8d1f3e7b2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'rejected_messages',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('broker_message_id', sa.String(length=200), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('raw_project_id', sa.String(length=200), nullable=True),
        sa.Column('template_key', sa.String(length=200), nullable=True),
        sa.Column('to_email', sa.String(length=320), nullable=True),
        sa.Column('payload_keys', sa.JSON(), nullable=False),
        sa.Column(
            'received_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_rejected_messages_broker_message_id'), 'rejected_messages',
        ['broker_message_id'], unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_rejected_messages_broker_message_id'), table_name='rejected_messages')
    op.drop_table('rejected_messages')
