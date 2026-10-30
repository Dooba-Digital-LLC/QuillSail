"""Keep a creator's newsletter blurb separate from the submitted article."""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("articles", sa.Column("newsletter_blurb", sa.String(5000), nullable=False, server_default=""))


def downgrade():
    with op.batch_alter_table("articles") as batch:
        batch.drop_column("newsletter_blurb")
