"""Create Agent drafts and immutable version snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("latest_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "name", name="uq_agent_workspace_name"),
    )
    op.create_index("ix_agents_workspace_id", "agents", ["workspace_id"])
    op.create_table(
        "agent_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("workspace_id", sa.String(100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("agent_id", "version", name="uq_agent_version"),
    )
    op.create_index("ix_agent_versions_agent_id", "agent_versions", ["agent_id"])
    op.create_index("ix_agent_versions_workspace_id", "agent_versions", ["workspace_id"])


def downgrade():
    op.drop_table("agent_versions")
    op.drop_table("agents")
