"""restrict app role from alembic_version table

Revision ID: a592bf0ced24
Revises: 5dc7767947fd
Create Date: 2026-07-07 21:34:21.680610

The blanket "ALL TABLES IN SCHEMA public" grant to ebm_app in the role-
creation migration also covered alembic_version — the app's runtime
role has no legitimate reason to read or write its own migration
version marker (only `alembic upgrade head`, run as the superuser at
container startup, touches it). Not a live exploit path today, but an
app-level SQL injection or a bug in future code could otherwise corrupt
migration state directly instead of just application data.
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'a592bf0ced24'
down_revision: Union[str, None] = '5dc7767947fd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("REVOKE ALL PRIVILEGES ON alembic_version FROM ebm_app")


def downgrade() -> None:
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON alembic_version TO ebm_app")
