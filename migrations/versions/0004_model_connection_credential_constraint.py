"""Require exactly one credential source per model connection."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_check_constraint(
        "ck_model_connection_one_credential",
        "model_connections",
        sa.text("(credential_ciphertext IS NOT NULL) <> (credential_ref IS NOT NULL)"),
    )


def downgrade():
    op.drop_constraint(
        "ck_model_connection_one_credential",
        "model_connections",
        type_="check",
    )
