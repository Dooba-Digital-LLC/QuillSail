"""Opt-in hosting connection and DNS ownership journal."""
from alembic import op
import sqlalchemy as sa
from datetime import datetime, UTC
from uuid import uuid4

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    settings = sa.table('settings', sa.column('key', sa.String()), sa.column('value', sa.JSON()), sa.column('updated_at', sa.DateTime()))
    connection = op.get_bind()
    if connection.execute(sa.select(settings.c.key).where(settings.c.key == 'system.instance_id')).first() is None:
        connection.execute(settings.insert().values(key='system.instance_id', value=uuid4().hex, updated_at=datetime.now(UTC).replace(tzinfo=None)))
    op.create_table('hosting_oauth_flows',
        sa.Column('state_hash', sa.String(64), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('auth_version', sa.Integer(), nullable=False),
        sa.Column('payload', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False))
    op.create_table('edge_receipts',
        sa.Column('id', sa.String(80), primary_key=True),
        sa.Column('received_at', sa.DateTime(), nullable=False))
    op.create_table('hosting_connection',
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('payload', sa.Text(), nullable=False),
        sa.Column('status', sa.String(32), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False))
    op.create_table('managed_records',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('zone_id', sa.String(64), nullable=False),
        sa.Column('provider_id', sa.String(64), nullable=False),
        sa.Column('name', sa.String(253), nullable=False),
        sa.Column('type', sa.String(10), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('previous', sa.Text(), nullable=True),
        sa.Column('purpose', sa.String(32), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('zone_id', 'provider_id', name='uq_managed_record'))


def downgrade():
    op.drop_table('hosting_oauth_flows')
    op.drop_table('edge_receipts')
    op.drop_table('managed_records')
    op.drop_table('hosting_connection')
