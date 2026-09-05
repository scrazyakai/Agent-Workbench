"""Create tool registry and immutable tool versions."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tools",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("tool_type", sa.String(50), nullable=False),
        sa.Column("draft", sa.JSON(), nullable=False),
        sa.Column("credential_ciphertext", sa.Text()),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("latest_version", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "name", name="uq_tool_workspace_name"),
    )
    op.create_index("ix_tools_workspace_id", "tools", ["workspace_id"])
    op.create_table(
        "tool_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tool_id", sa.Uuid(), sa.ForeignKey("tools.id"), nullable=False),
        sa.Column("workspace_id", sa.String(100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tool_id", "version", name="uq_tool_version"),
    )
    op.create_index("ix_tool_versions_tool_id", "tool_versions", ["tool_id"])
    op.create_index("ix_tool_versions_workspace_id", "tool_versions", ["workspace_id"])


def downgrade():
    op.drop_table("tool_versions")
    op.drop_table("tools")
