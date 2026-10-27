"""Encrypted publication email connections and one-use OAuth handshakes."""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "email_connection",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.String(32), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "email_oauth_flows",
        sa.Column("state_hash", sa.String(64), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("auth_version", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_email_oauth_flows_created_at", "email_oauth_flows", ["created_at"])


def downgrade():
    op.drop_table("email_oauth_flows")
    op.drop_table("email_connection")
