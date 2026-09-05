"""Store model API keys as AES-GCM ciphertext."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("model_connections", sa.Column("credential_ciphertext", sa.Text()))
    op.alter_column("model_connections", "credential_ref", nullable=True)


def downgrade():
    op.execute("DELETE FROM model_connections WHERE credential_ref IS NULL")
    op.alter_column("model_connections", "credential_ref", nullable=False)
    op.drop_column("model_connections", "credential_ciphertext")
