"""drop dead sqs_queue_url, add Project.external and ApiKey.expires_at

Project.sqs_queue_url was dead weight left over from the SQS/ElasticMQ era --
required on every project but never read at runtime since the move to
Celery/Redis (see a3f7c1d92b6e). Per-project Celery queues are now derived
from `Project.slug` (app/projects/models.py::queue_name_for), so no queue
column is needed at all.

Project.external + ApiKey.expires_at back the new external-project flow:
external projects get an API key that expires 30 days after creation, while
internal projects keep the existing never-expiring behavior.

Revision ID: fcf8813766b8
Revises: a3f7c1d92b6e
Create Date: 2026-09-17 10:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'fcf8813766b8'
down_revision: str | None = 'a3f7c1d92b6e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column('projects', 'sqs_queue_url')
    op.add_column(
        'projects',
        sa.Column('external', sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        'api_keys', sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('api_keys', 'expires_at')
    op.drop_column('projects', 'external')
    op.add_column(
        'projects',
        sa.Column('sqs_queue_url', sa.String(length=500), server_default='', nullable=False),
    )
