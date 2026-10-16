"""HTTP send API: message columns, idempotency index, attachments (DVB-309 / S4)

Revision ID: c7e3a9b5d1f4
Revises: b5d1f7a9c3e4
Create Date: 2026-10-16 06:30:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c7e3a9b5d1f4'
down_revision: str | None = 'b5d1f7a9c3e4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = [
    sa.Column('idempotency_key', sa.String(length=100), nullable=True),
    sa.Column('request_hash', sa.String(length=64), nullable=True),
    sa.Column('email_type', sa.String(length=100), nullable=True),
    sa.Column('recipients', sa.JSON(), nullable=True),
    sa.Column('reply_to', sa.String(length=320), nullable=True),
    sa.Column('raw_subject', sa.String(length=500), nullable=True),
    sa.Column('raw_html', sa.Text(), nullable=True),
    sa.Column('raw_text', sa.Text(), nullable=True),
    sa.Column('headers', sa.JSON(), nullable=True),
    sa.Column('source', sa.String(length=200), nullable=True),
    sa.Column('metadata', sa.JSON(), nullable=True),
]


def upgrade() -> None:
    for column in _COLUMNS:
        op.add_column('messages', column)
    op.create_index(
        'uq_messages_project_idempotency_key', 'messages', ['project_id', 'idempotency_key'],
        unique=True, postgresql_where=sa.text('idempotency_key IS NOT NULL'),
    )
    op.create_table(
        'message_attachments',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('message_id', sa.Uuid(), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('content_type', sa.String(length=100), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('data', sa.LargeBinary(), nullable=False),
        sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_message_attachments_message_id'), 'message_attachments', ['message_id']
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_message_attachments_message_id'), table_name='message_attachments')
    op.drop_table('message_attachments')
    op.drop_index('uq_messages_project_idempotency_key', table_name='messages')
    for column in reversed(_COLUMNS):
        op.drop_column('messages', column.name)
