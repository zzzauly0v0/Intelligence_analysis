"""Create users and sessions

Baseline schema for the SQLAlchemy models in ``app.db.models``. It replaces the
old SQLModel migrations (``user``/``item`` tables), so an existing database from
before the refactor must be dropped and recreated rather than upgraded.

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-08-15

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=True),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("is_app_admin", sa.Boolean(), nullable=False),
        sa.Column("avatar_url", sa.String(length=500), nullable=True),
        sa.Column("onboarding_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("oauth_provider", sa.String(length=32), nullable=True),
        sa.Column("oauth_id", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="users_pkey"),
    )
    op.create_index("users_email_idx", "users", ["email"], unique=True)
    op.create_index("users_oauth_provider_idx", "users", ["oauth_provider"])
    op.create_index("users_oauth_id_idx", "users", ["oauth_id"])

    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("refresh_token_hash", sa.String(length=255), nullable=False),
        sa.Column("device_name", sa.String(length=255), nullable=True),
        sa.Column("device_type", sa.String(length=50), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="sessions_user_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="sessions_pkey"),
    )
    op.create_index("sessions_user_id_idx", "sessions", ["user_id"])
    op.create_index(
        "sessions_refresh_token_hash_idx", "sessions", ["refresh_token_hash"]
    )


def downgrade() -> None:
    op.drop_index("sessions_refresh_token_hash_idx", table_name="sessions")
    op.drop_index("sessions_user_id_idx", table_name="sessions")
    op.drop_table("sessions")
    op.drop_index("users_oauth_id_idx", table_name="users")
    op.drop_index("users_oauth_provider_idx", table_name="users")
    op.drop_index("users_email_idx", table_name="users")
    op.drop_table("users")
