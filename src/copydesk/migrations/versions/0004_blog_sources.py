"""Attribution for blog posts offered to a separate newsletter."""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("articles", sa.Column("source_url", sa.String(2048), nullable=False, server_default=""))
    op.add_column("articles", sa.Column("source_author", sa.String(200), nullable=False, server_default=""))


def downgrade():
    with op.batch_alter_table("articles") as batch:
        batch.drop_column("source_author")
        batch.drop_column("source_url")
