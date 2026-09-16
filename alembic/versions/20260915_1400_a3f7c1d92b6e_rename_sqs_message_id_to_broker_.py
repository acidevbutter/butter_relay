"""rename sqs_message_id to broker_message_id

The worker moved off SQS (ElasticMQ locally, real SQS never shipped) onto a
Celery/Redis broker -- see app/worker/tasks.py. The idempotency key is now a
Celery task id, not an SQS MessageId, so the column gets a broker-neutral
name instead of implying an SQS dependency that no longer exists.

Revision ID: a3f7c1d92b6e
Revises: dfb1bc6699a9
Create Date: 2026-09-15 14:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = 'a3f7c1d92b6e'
down_revision: str | None = 'dfb1bc6699a9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column('messages', 'sqs_message_id', new_column_name='broker_message_id')
    op.drop_index(op.f('ix_messages_sqs_message_id'), table_name='messages')
    op.create_index(
        op.f('ix_messages_broker_message_id'), 'messages', ['broker_message_id'], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_messages_broker_message_id'), table_name='messages')
    op.alter_column('messages', 'broker_message_id', new_column_name='sqs_message_id')
    op.create_index(
        op.f('ix_messages_sqs_message_id'), 'messages', ['sqs_message_id'], unique=False
    )
