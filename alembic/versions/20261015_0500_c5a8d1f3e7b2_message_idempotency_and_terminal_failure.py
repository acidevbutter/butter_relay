"""messages: unique (project_id, broker_message_id) + terminal_failure flag

The Celery task id is the idempotency key of a logical message, so the database now
enforces one row per (project_id, broker_message_id): two workers racing on a
redelivered task can no longer both insert. Before the retry fix (butter_relay#35) every
retry inserted its own row with the same task id; to make the constraint creatable, all
but the most recent row of each duplicate group have their broker_message_id cleared
(rows and their history are kept, they just stop being the idempotency anchor).

`terminal_failure` marks messages no further automatic attempt will touch (permanent
provider error, blocked recipient, retries exhausted); existing failed rows default to
false (unknown), which only means a redelivery may still retry them, as before.

Revision ID: c5a8d1f3e7b2
Revises: b7d4e2a91c3f
Create Date: 2026-10-15 05:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c5a8d1f3e7b2'
down_revision: str | None = 'b7d4e2a91c3f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


DEDUPE_SQL = """
UPDATE messages SET broker_message_id = NULL
WHERE id IN (
    SELECT id FROM (
        SELECT id,
               ROW_NUMBER() OVER (
                   PARTITION BY project_id, broker_message_id
                   ORDER BY received_at DESC, id
               ) AS rn
        FROM messages
        WHERE broker_message_id IS NOT NULL
    ) ranked
    WHERE ranked.rn > 1
)
"""


def upgrade() -> None:
    op.add_column(
        'messages',
        sa.Column('terminal_failure', sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.execute(DEDUPE_SQL)
    op.create_unique_constraint(
        'uq_messages_project_broker_message_id', 'messages', ['project_id', 'broker_message_id']
    )


def downgrade() -> None:
    op.drop_constraint('uq_messages_project_broker_message_id', 'messages', type_='unique')
    op.drop_column('messages', 'terminal_failure')
