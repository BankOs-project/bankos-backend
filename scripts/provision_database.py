"""Prepara el esquema y concede permisos a un rol de ejecución existente."""

import argparse
import asyncio
import os

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import engine
from app.core.database_setup import prepare_schema, require_audit_append_only
from app.main import app  # noqa: F401 - registra los modelos del backend.


async def provision(role: str, password: str | None = None) -> None:
    engine.echo = False
    async with engine.begin() as connection:
        login_role = await connection.scalar(text(
            "SELECT rolcanlogin AND rolname <> current_user FROM pg_roles WHERE rolname = :role"
        ), {"role": role})
        if login_role is None and password:
            statement = await connection.scalar(text(
                "SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', CAST(:role AS text), CAST(:password AS text))"
            ), {"role": role, "password": password})
            try:
                await connection.exec_driver_sql(statement)
            except SQLAlchemyError:
                raise RuntimeError("No se pudo crear el rol de ejecución de PostgreSQL.") from None
        elif login_role is not True:
            raise RuntimeError("Crea un rol de login distinto del rol dueño antes de otorgar permisos.")

        await prepare_schema(connection)
        identifier = connection.dialect.identifier_preparer.quote_identifier(role)
        await connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {identifier}"))
        await connection.execute(text("REVOKE ALL ON public.audit_logs FROM PUBLIC"))
        await connection.execute(text(f"REVOKE ALL ON public.audit_logs FROM {identifier}"))
        await connection.execute(text(f"""
            GRANT SELECT, INSERT ON public.audit_logs, public.transactions, public.users, public.persons TO {identifier}
        """))
        await connection.execute(text(f"""
            GRANT SELECT, INSERT, UPDATE ON public.tenants, public.accounts,
                public.transaction_idempotency_receipts, public.registration_challenges, public.transfer_quotes TO {identifier}
        """))
        await connection.execute(text(f"GRANT UPDATE (email_verified_at) ON public.users TO {identifier}"))
        await require_audit_append_only(connection, role)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", help="Rol de ejecución dedicado que usará la API")
    asyncio.run(provision(parser.parse_args().role, os.getenv("BANKOS_APP_DB_PASSWORD")))
