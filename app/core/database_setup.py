"""Preparación del esquema y comprobación de los permisos del rol de ejecución."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.database import Base


async def prepare_schema(connection: AsyncConnection) -> None:
    await connection.run_sync(Base.metadata.create_all)
    await connection.execute(text("""
        ALTER TABLE public.tenants
        ADD COLUMN IF NOT EXISTS public_id uuid NOT NULL DEFAULT gen_random_uuid(),
        ADD COLUMN IF NOT EXISTS directory_enabled boolean NOT NULL DEFAULT false,
        ADD COLUMN IF NOT EXISTS logo_url varchar(2048)
    """))
    await connection.execute(text("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_tenants_public_id ON public.tenants (public_id)
    """))
    await connection.execute(text("""
        ALTER TABLE public.transactions
        ADD COLUMN IF NOT EXISTS actor_user_id uuid,
        ADD COLUMN IF NOT EXISTS failure_code varchar(100),
        ADD COLUMN IF NOT EXISTS transfer_mode varchar(20),
        ADD COLUMN IF NOT EXISTS transfer_reference uuid
    """))
    await connection.execute(text("""
        ALTER TABLE public.users
        ADD COLUMN IF NOT EXISTS person_id uuid REFERENCES public.persons (id),
        ADD COLUMN IF NOT EXISTS email_verified_at timestamptz
    """))
    await connection.execute(text("""
        WITH pending AS MATERIALIZED (
            SELECT id AS user_id, gen_random_uuid() AS person_id
            FROM public.users WHERE person_id IS NULL
        ), created AS (
            INSERT INTO public.persons (id) SELECT person_id FROM pending RETURNING id
        )
        UPDATE public.users AS users SET person_id = pending.person_id
        FROM pending JOIN created ON created.id = pending.person_id
        WHERE users.id = pending.user_id
    """))
    await connection.execute(text("ALTER TABLE public.users ALTER COLUMN person_id SET NOT NULL"))
    await connection.execute(text("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_users_tenant_person ON public.users (tenant_id, person_id)
    """))
    await connection.execute(text("""
        ALTER TABLE public.accounts
        ADD COLUMN IF NOT EXISTS reactivated_at timestamptz,
        ADD COLUMN IF NOT EXISTS can_deactivate_at timestamptz,
        ADD COLUMN IF NOT EXISTS requires_admin_reactivation boolean
    """))
    await connection.execute(text("""
        UPDATE public.accounts AS account SET requires_admin_reactivation =
            CASE WHEN account.status = 'INACTIVE' THEN COALESCE((
                SELECT COALESCE(audit.new_state->>'actor_role', actor.role) = 'administrador'
                FROM public.audit_logs AS audit
                LEFT JOIN public.users AS actor ON actor.id = audit.user_id AND actor.tenant_id = audit.tenant_id
                WHERE audit.tenant_id = account.tenant_id
                  AND audit.action = 'ACCOUNT_DEACTIVATED'
                  AND audit.new_state->>'account_number' = account.account_number
                ORDER BY audit.created_at DESC, audit.id DESC
                LIMIT 1
            ), false) ELSE false END
        WHERE account.requires_admin_reactivation IS NULL
    """))
    await connection.execute(text("""
        ALTER TABLE public.accounts
        ALTER COLUMN requires_admin_reactivation SET DEFAULT false,
        ALTER COLUMN requires_admin_reactivation SET NOT NULL
    """))


async def require_audit_append_only(connection: AsyncConnection, role: str | None = None) -> None:
    allowed = await connection.scalar(text("""
        SELECT
            has_table_privilege(r.oid, c.oid, 'SELECT')
            AND has_table_privilege(r.oid, c.oid, 'INSERT')
            AND NOT has_table_privilege(r.oid, c.oid, 'UPDATE, DELETE, TRUNCATE, TRIGGER')
            AND NOT has_any_column_privilege(r.oid, c.oid, 'UPDATE')
            AND NOT (r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication OR r.rolbypassrls)
            AND r.oid NOT IN (c.relowner, n.nspowner, d.datdba)
            AND NOT EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.member = r.oid)
        FROM pg_roles r
        CROSS JOIN pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
        JOIN pg_database d ON d.datname = current_database()
        WHERE r.rolname = COALESCE(CAST(:role AS name), current_user)
          AND c.relname = 'audit_logs'
    """), {"role": role})
    if allowed is not True:
        raise RuntimeError("Configura un rol de aplicación con SELECT e INSERT sobre audit_logs y sin permisos de modificación.")
