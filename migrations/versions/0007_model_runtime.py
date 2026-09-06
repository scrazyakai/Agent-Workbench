"""Pin execution configuration, track budgets and fence worker claims."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "runs", sa.Column("lease_generation", sa.Integer(), nullable=False, server_default="0")
    )
    # Existing runs must retain their original deterministic semantics on recovery.
    op.add_column(
        "runs",
        sa.Column("execution_mode", sa.String(24), nullable=False, server_default="deterministic"),
    )
    op.alter_column("runs", "execution_mode", server_default="model")
    op.add_column(
        "runs", sa.Column("config_snapshot", sa.JSON(), nullable=False, server_default="{}")
    )
    op.add_column("runs", sa.Column("usage", sa.JSON(), nullable=False, server_default="{}"))


def downgrade():
    for name in ("usage", "config_snapshot", "execution_mode", "lease_generation"):
        op.drop_column("runs", name)
