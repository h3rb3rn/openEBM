"""create restricted app role

Revision ID: f0f3415ee6b4
Revises: 10ecbf2372c3
Create Date: 2026-07-07 21:18:09.145634

The row-level-security policies from the previous migration are inert
against a superuser connection — Postgres superusers (and any role with
BYPASSRLS) skip RLS entirely, FORCE ROW LEVEL SECURITY notwithstanding.
This project's POSTGRES_USER is the cluster bootstrap role, which the
official postgres Docker image always makes a superuser, and the app
has been connecting as that same role. Discovered by actually testing
cross-tenant isolation with a raw SQL query, not just checking that
`\\d+` printed the policy — the policy existed and did nothing.

Creates a NOSUPERUSER / NOBYPASSRLS role for the app's runtime
connection, with exactly the privileges it needs (no DDL, no role
management). Migrations continue running as the superuser (DDL like
CREATE POLICY needs elevated privileges the restricted role
intentionally doesn't have). The password is set separately at
container startup from APP_DB_PASSWORD (see app-entrypoint.sh) — never
embedded in a migration file.
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'f0f3415ee6b4'
down_revision: Union[str, None] = '10ecbf2372c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ebm_app') THEN
                CREATE ROLE ebm_app WITH LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT;
            END IF;
        END
        $$;
    """)
    op.execute("GRANT CONNECT ON DATABASE ebm_db TO ebm_app")
    op.execute("GRANT USAGE ON SCHEMA public TO ebm_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ebm_app")
    # Anything created by a later migration is granted the same access
    # automatically, so this doesn't need updating every time a table is added.
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ebm_app")


def downgrade() -> None:
    op.execute("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM ebm_app")
    op.execute("REVOKE USAGE ON SCHEMA public FROM ebm_app")
    op.execute("REVOKE CONNECT ON DATABASE ebm_db FROM ebm_app")
    op.execute("DROP ROLE IF EXISTS ebm_app")
