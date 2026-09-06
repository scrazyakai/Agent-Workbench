"""Create durable Agent runtime records."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.String(100), nullable=False),
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column(
            "agent_version_id", sa.Uuid(), sa.ForeignKey("agent_versions.id"), nullable=False
        ),
        sa.Column("agent_version", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.String(200)),
        sa.Column("idempotency_key", sa.String(200)),
        sa.Column("request_fingerprint", sa.String(64)),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("result", sa.JSON()),
        sa.Column("error", sa.JSON()),
        sa.Column("worker_id", sa.String(200)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("execution_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recovery_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_run_workspace_idempotency"),
    )
    op.create_index("ix_runs_workspace_id", "runs", ["workspace_id"])
    op.create_index("ix_runs_agent_id", "runs", ["agent_id"])
    op.create_index("ix_runs_agent_version_id", "runs", ["agent_version_id"])
    op.create_index("ix_runs_thread_id", "runs", ["thread_id"])
    op.create_index("ix_runs_status", "runs", ["status"])
    op.create_index("ix_runs_lease_expires_at", "runs", ["lease_expires_at"])
    op.create_index("ix_runs_status_created", "runs", ["status", "created_at"])
    op.create_index(
        "ix_runs_workspace_thread_status", "runs", ["workspace_id", "thread_id", "status"]
    )

    op.create_table(
        "step_executions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("workspace_id", sa.String(100), nullable=False),
        sa.Column("step_key", sa.String(100), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_summary", sa.JSON()),
        sa.Column("output_summary", sa.JSON()),
        sa.Column("error_code", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("run_id", "step_key", name="uq_step_run_key"),
    )
    op.create_index("ix_step_executions_run_id", "step_executions", ["run_id"])
    op.create_index("ix_step_executions_workspace_id", "step_executions", ["workspace_id"])

    op.create_table(
        "checkpoints",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("workspace_id", sa.String(100), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "sequence", name="uq_checkpoint_run_sequence"),
    )
    op.create_index("ix_checkpoints_run_id", "checkpoints", ["run_id"])
    op.create_index("ix_checkpoints_workspace_id", "checkpoints", ["workspace_id"])

    op.create_table(
        "run_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("workspace_id", sa.String(100), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "sequence", name="uq_run_event_sequence"),
    )
    op.create_index("ix_run_events_run_id", "run_events", ["run_id"])
    op.create_index("ix_run_events_workspace_id", "run_events", ["workspace_id"])


def downgrade():
    op.drop_table("run_events")
    op.drop_table("checkpoints")
    op.drop_table("step_executions")
    op.drop_table("runs")
