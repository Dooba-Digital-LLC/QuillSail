"""Competitions, moderated entries, judges, scores and reader ballots."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import LONGTEXT

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("competitions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
        sa.Column("rules", sa.Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("entries_close_at", sa.DateTime()),
        sa.Column("ends_at", sa.DateTime()),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("results_at", sa.DateTime()),
        sa.CheckConstraint("mode IN ('judges', 'readers', 'both')", name="ck_competition_mode"),
        sa.CheckConstraint("status IN ('draft', 'submissions', 'judging', 'closed', 'results')", name="ck_competition_status"),
    )
    op.create_index("ix_competitions_status", "competitions", ["status"])
    op.create_table("competition_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("competition_id", sa.Integer(), sa.ForeignKey("competitions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
        sa.Column("project_url", sa.String(2048), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("moderation_note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("competition_id", "user_id", name="uq_competition_entrant"),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected', 'withdrawn')", name="ck_competition_entry_status"),
    )
    op.create_index("ix_competition_entries_competition_id", "competition_entries", ["competition_id"])
    op.create_table("competition_judges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("competition_id", sa.Integer(), sa.ForeignKey("competitions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("competition_id", "user_id", name="uq_competition_judge"),
    )
    op.create_index("ix_competition_judges_competition_id", "competition_judges", ["competition_id"])
    op.create_table("competition_scores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entry_id", sa.Integer(), sa.ForeignKey("competition_entries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("judge_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("entry_id", "judge_id", name="uq_competition_score"),
        sa.CheckConstraint("score >= 0 AND score <= 100", name="ck_competition_score_range"),
    )
    op.create_index("ix_competition_scores_entry_id", "competition_scores", ["entry_id"])
    op.create_table("competition_votes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("competition_id", sa.Integer(), sa.ForeignKey("competitions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entry_id", sa.Integer(), sa.ForeignKey("competition_entries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("competition_id", "user_id", name="uq_competition_vote"),
    )
    op.create_index("ix_competition_votes_competition_id", "competition_votes", ["competition_id"])
    op.create_index("ix_competition_votes_entry_id", "competition_votes", ["entry_id"])


def downgrade():
    for table in ("competition_votes", "competition_scores", "competition_judges", "competition_entries", "competitions"):
        op.drop_table(table)
