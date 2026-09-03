"""restrict app role audit log to append only

Revision ID: 5dc7767947fd
Revises: f0f3415ee6b4
Create Date: 2026-07-07 21:25:21.657893

audit_logs is an immutable compliance trail (§ 203 StGB / GDPR
accountability — see the model's docstring); the app only ever
constructs and inserts AuditLog rows, never updates or deletes one.
The previous migration granted UPDATE/DELETE on all tables uniformly
for simplicity, which left the app's own database role able to alter
or erase its own audit history — exactly the production checklist item
this closes ("audit_logs table has no UPDATE/DELETE grants for the app
user").
"""
from typing import Sequence, Union

from alembic import op

revision: str = '5dc7767947fd'
down_revision: Union[str, None] = 'f0f3415ee6b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("REVOKE UPDATE, DELETE ON audit_logs FROM ebm_app")


def downgrade() -> None:
    op.execute("GRANT UPDATE, DELETE ON audit_logs TO ebm_app")
