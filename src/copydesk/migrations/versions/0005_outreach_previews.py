"""Private issue previews and a business outreach inbox."""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "issue_previews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("issue_id", sa.Integer(), sa.ForeignKey("issues.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("issue_id", name="uq_issue_previews_issue_id"),
        sa.UniqueConstraint("slug", name="uq_issue_previews_slug"),
    )
    op.create_table(
        "business_inquiries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization", sa.String(160), nullable=False),
        sa.Column("contact_name", sa.String(100), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("website_url", sa.String(2048), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("skills", sa.String(500), nullable=False),
        sa.Column("location", sa.String(200), nullable=False),
        sa.Column("compensation", sa.String(200), nullable=False),
        sa.Column("deadline", sa.Date(), nullable=True),
        sa.Column("application_url", sa.String(2048), nullable=False),
        sa.Column("share_contact", sa.Boolean(), nullable=False),
        sa.Column("consented_at", sa.DateTime(), nullable=False),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("staff_notes", sa.Text(), nullable=False),
        sa.Column("article_id", sa.Integer(), sa.ForeignKey("articles.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_business_inquiries_status", "business_inquiries", ["status"])


def downgrade():
    op.drop_table("business_inquiries")
    op.drop_table("issue_previews")
