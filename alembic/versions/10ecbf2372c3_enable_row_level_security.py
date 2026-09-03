"""enable row level security

Revision ID: 10ecbf2372c3
Revises: b926dcfba22b
Create Date: 2026-07-07 21:11:04.147919

Adds a defense-in-depth backstop against cross-tenant data leaks: even
if a future endpoint forgets a `WHERE tenant_id = ...` filter, Postgres
itself now refuses to return rows outside the session's configured
tenant. This does NOT replace the existing application-layer filtering
(every query still filters explicitly) — it's a second, independent
layer that fails closed: if `app.tenant_id` isn't set on a session, the
policy matches nothing rather than everything.

Scope: patients, case_files, gop_suggestions, audit_logs — tables that
hold clinical/compliance data and are always queried within an
already-established tenant context.

Deliberately NOT applied to users or api_keys: both are looked up
*across* tenants by design (login resolves a user by email before the
tenant is known; API-key auth resolves an actor by key hash the same
way) — RLS keyed on a not-yet-known tenant would break login/API-key
auth entirely. tenant_id is still explicitly filtered in every query
against those tables at the application layer, as before.
"""
from typing import Sequence, Union

from alembic import op

revision: str = '10ecbf2372c3'
down_revision: Union[str, None] = 'b926dcfba22b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DIRECT_TABLES = ["patients", "case_files", "audit_logs"]


def upgrade() -> None:
    for table in _DIRECT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (tenant_id = current_setting('app.tenant_id', true))
        """)

    # gop_suggestions has no direct tenant_id column (only case_file_id) —
    # scope it via a subquery against case_files, which is itself protected
    # by the policy above.
    op.execute("ALTER TABLE gop_suggestions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE gop_suggestions FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON gop_suggestions
        USING (
            case_file_id IN (
                SELECT id FROM case_files
                WHERE tenant_id = current_setting('app.tenant_id', true)
            )
        )
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON gop_suggestions")
    op.execute("ALTER TABLE gop_suggestions DISABLE ROW LEVEL SECURITY")
    for table in reversed(_DIRECT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
