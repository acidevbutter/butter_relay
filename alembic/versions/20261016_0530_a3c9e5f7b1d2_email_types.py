"""email_types catalogue + projects.allow_undeclared_types (DVB-307 / S2)

Revision ID: a3c9e5f7b1d2
Revises: f1a8c4d6b3e9
Create Date: 2026-10-16 05:30:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a3c9e5f7b1d2'
down_revision: str | None = 'f1a8c4d6b3e9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'email_types',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('key', sa.String(length=100), nullable=False),
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.Column('category', sa.String(length=20), nullable=False),
        sa.Column('mode', sa.String(length=10), nullable=False),
        sa.Column('locale', sa.String(length=20), nullable=True),
        sa.Column('sample_payload', sa.JSON(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'key', name='uq_email_types_project_key'),
    )
    op.create_index(op.f('ix_email_types_project_id'), 'email_types', ['project_id'])
    op.add_column(
        'projects',
        sa.Column(
            'allow_undeclared_types', sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column('projects', 'allow_undeclared_types')
    op.drop_index(op.f('ix_email_types_project_id'), table_name='email_types')
    op.drop_table('email_types')
