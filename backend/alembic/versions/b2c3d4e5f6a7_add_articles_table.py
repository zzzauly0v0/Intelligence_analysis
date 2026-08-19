"""Add articles table

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-18

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "articles",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("site_name", sa.String(length=200), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("publish_date", sa.Date(), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("group_type", sa.String(length=50), nullable=False, server_default="competitor"),
        sa.Column("is_external", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("embedding_done", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="articles_pkey"),
        sa.UniqueConstraint("url", name="articles_url_key"),
    )
    op.create_index("articles_site_name_idx", "articles", ["site_name"])
    op.create_index("articles_publish_date_idx", "articles", ["publish_date"])
    op.create_index("articles_group_type_idx", "articles", ["group_type"])


def downgrade() -> None:
    op.drop_index("articles_group_type_idx", table_name="articles")
    op.drop_index("articles_publish_date_idx", table_name="articles")
    op.drop_index("articles_site_name_idx", table_name="articles")
    op.drop_table("articles")
