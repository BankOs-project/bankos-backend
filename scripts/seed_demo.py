"""
BankOS — Script de Demostración (seed_demo.py)
===============================================
Pobla la base de datos con 2 tenants independientes y completamente aislados,
cada uno con su administrador, 2 clientes y varias cuentas listas para demostrar
todos los escenarios del reto en vivo:

  Tenant 1: banco-andino
    - Comisión: 1.5% sobre transferencias
    - Moneda base: COP
    - Tasas: USD→COP=4200, EUR→COP=4500, USD→EUR=0.9200

  Tenant 2: cooperativa-caribe
    - Comisión: FIJA de 5000 COP
    - Moneda base: USD
    - Tasas: USD→COP=4150, EUR→USD=1.0870

Uso:
  # Con Docker Compose activo:
  docker compose exec api python scripts/seed_demo.py

  # Localmente (con .env configurado):
  uv run python scripts/seed_demo.py
"""
from __future__ import annotations

import asyncio
import sys
import os

# Asegurar que el directorio raíz del proyecto esté en PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.database import Base
from app.core.security import get_password_hash

# Modelos ORM (importar todos para que Base.metadata los conozca)
from app.modules.tenants.infrastructure.models import TenantModel
from app.modules.auth_users.infrastructure.models import UserModel
from app.modules.accounts.infrastructure.models import AccountModel
from app.modules.transactions.infrastructure.models import TransactionModel  # noqa: F401
from app.modules.audit.infrastructure.models import AuditLogModel            # noqa: F401

import uuid
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bankos.seed")


# ---------------------------------------------------------------------------
# Datos de demostración
# ---------------------------------------------------------------------------

TENANTS = [
    {
        "id": uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001"),
        "name": "Banco Andino S.A.",
        "slug": "banco-andino",
        "max_transaction_limit": Decimal("50000000.0000"),   # 50M COP
        "fee_type": "PERCENTAGE",
        "fee_value": Decimal("1.5000"),
        "base_currency": "COP",
        "exchange_rates": {
            "USD_COP": "4200.0000",
            "EUR_COP": "4500.0000",
            "USD_EUR": "0.9200",
            "EUR_USD": "1.0870",
            "COP_USD": "0.0002381",
            "COP_EUR": "0.0002222",
        },
        "webhook_url": "https://webhook.site/banco-andino-demo",
    },
    {
        "id": uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002"),
        "name": "Cooperativa Caribe Ltda.",
        "slug": "cooperativa-caribe",
        "max_transaction_limit": Decimal("10000.0000"),      # 10K USD
        "fee_type": "FIXED",
        "fee_value": Decimal("2.5000"),                      # 2.5 USD fijo
        "base_currency": "USD",
        "exchange_rates": {
            "USD_COP": "4150.0000",
            "EUR_USD": "1.0870",
            "USD_EUR": "0.9200",
            "COP_USD": "0.0002410",
            "EUR_COP": "4500.0000",
            "COP_EUR": "0.0002222",
        },
        "webhook_url": None,
    },
]

USERS = {
    "banco-andino": [
        {
            "id": uuid.UUID("aaaaaaaa-0001-0000-0000-000000000001"),
            "email": "admin@banco-andino.com",
            "password": "Admin123!",
            "full_name": "Adriano Torres (Admin)",
            "role": "administrador",
        },
        {
            "id": uuid.UUID("aaaaaaaa-0002-0000-0000-000000000001"),
            "email": "juan@banco-andino.com",
            "password": "Cliente123!",
            "full_name": "Juan Pérez Gómez",
            "role": "cliente",
        },
        {
            "id": uuid.UUID("aaaaaaaa-0003-0000-0000-000000000001"),
            "email": "jaysir@banco-andino.com",
            "password": "Cliente123!",
            "full_name": "Jaysir Martínez Ruiz",
            "role": "cliente",
        },
    ],
    "cooperativa-caribe": [
        {
            "id": uuid.UUID("bbbbbbbb-0001-0000-0000-000000000002"),
            "email": "admin@coop-caribe.com",
            "password": "Admin123!",
            "full_name": "Administrador Caribe",
            "role": "administrador",
        },
        {
            "id": uuid.UUID("bbbbbbbb-0002-0000-0000-000000000002"),
            "email": "cliente1@coop-caribe.com",
            "password": "Cliente123!",
            "full_name": "María Fernanda Lora",
            "role": "cliente",
        },
        {
            "id": uuid.UUID("bbbbbbbb-0003-0000-0000-000000000002"),
            "email": "cliente2@coop-caribe.com",
            "password": "Cliente123!",
            "full_name": "Carlos Benedetti Díaz",
            "role": "cliente",
        },
    ],
}

ACCOUNTS = {
    "banco-andino": [
        # Juan — cuenta COP activa con saldo generoso
        {
            "id": uuid.UUID("aaaaaaaa-ac01-0000-0000-000000000001"),
            "account_number": "1000000001",
            "owner_key": "juan@banco-andino.com",
            "balance": Decimal("5000000.0000"),
            "currency": "COP",
            "status": "ACTIVE",
        },
        # Juan — cuenta USD activa
        {
            "id": uuid.UUID("aaaaaaaa-ac02-0000-0000-000000000001"),
            "account_number": "1000000002",
            "owner_key": "juan@banco-andino.com",
            "balance": Decimal("1500.0000"),
            "currency": "USD",
            "status": "ACTIVE",
        },
        # Jaysir — cuenta COP activa
        {
            "id": uuid.UUID("aaaaaaaa-ac03-0000-0000-000000000001"),
            "account_number": "1000000003",
            "owner_key": "jaysir@banco-andino.com",
            "balance": Decimal("2500000.0000"),
            "currency": "COP",
            "status": "ACTIVE",
        },
        # Jaysir — cuenta EUR activa
        {
            "id": uuid.UUID("aaaaaaaa-ac04-0000-0000-000000000001"),
            "account_number": "1000000004",
            "owner_key": "jaysir@banco-andino.com",
            "balance": Decimal("800.0000"),
            "currency": "EUR",
            "status": "ACTIVE",
        },
        # Juan — cuenta INACTIVA (para demostrar soft-delete)
        {
            "id": uuid.UUID("aaaaaaaa-ac05-0000-0000-000000000001"),
            "account_number": "1000000005",
            "owner_key": "juan@banco-andino.com",
            "balance": Decimal("0.0000"),
            "currency": "COP",
            "status": "INACTIVE",
        },
    ],
    "cooperativa-caribe": [
        # María — cuenta USD activa
        {
            "id": uuid.UUID("bbbbbbbb-ac01-0000-0000-000000000002"),
            "account_number": "2000000001",
            "owner_key": "cliente1@coop-caribe.com",
            "balance": Decimal("3000.0000"),
            "currency": "USD",
            "status": "ACTIVE",
        },
        # Carlos — cuenta USD activa
        {
            "id": uuid.UUID("bbbbbbbb-ac02-0000-0000-000000000002"),
            "account_number": "2000000002",
            "owner_key": "cliente2@coop-caribe.com",
            "balance": Decimal("1200.0000"),
            "currency": "USD",
            "status": "ACTIVE",
        },
        # María — cuenta COP activa para demostrar conversión
        {
            "id": uuid.UUID("bbbbbbbb-ac03-0000-0000-000000000002"),
            "account_number": "2000000003",
            "owner_key": "cliente1@coop-caribe.com",
            "balance": Decimal("4500000.0000"),
            "currency": "COP",
            "status": "ACTIVE",
        },
        # Carlos — cuenta BLOQUEADA
        {
            "id": uuid.UUID("bbbbbbbb-ac04-0000-0000-000000000002"),
            "account_number": "2000000004",
            "owner_key": "cliente2@coop-caribe.com",
            "balance": Decimal("500.0000"),
            "currency": "USD",
            "status": "BLOCKED",
        },
    ],
}


from sqlalchemy import select

# ---------------------------------------------------------------------------
# Lógica de seed
# ---------------------------------------------------------------------------

async def seed(session: AsyncSession) -> None:
    # 0. Verificación de idempotencia: si ya existen tenants, no duplicar
    existing_tenant = await session.execute(select(TenantModel).limit(1))
    if existing_tenant.scalar_one_or_none() is not None:
        log.info("ℹ️ Los datos de demostración ya existen en la base de datos (no se requieren cambios).")
        _print_summary()
        return

    now = datetime.now(timezone.utc)

    # 1. Tenants
    for t_data in TENANTS:
        slug = t_data["slug"]
        log.info("→ Creando tenant: %s", slug)
        tenant = TenantModel(
            id=t_data["id"],
            name=t_data["name"],
            slug=slug,
            max_transaction_limit=t_data["max_transaction_limit"],
            fee_type=t_data["fee_type"],
            fee_value=t_data["fee_value"],
            base_currency=t_data["base_currency"],
            exchange_rates=t_data["exchange_rates"],
            webhook_url=t_data["webhook_url"],
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        session.add(tenant)
    await session.flush()

    # 2. Users
    email_to_user_id: dict[str, uuid.UUID] = {}
    for t_data in TENANTS:
        slug = t_data["slug"]
        for u_data in USERS[slug]:
            log.info("  → Usuario [%s]: %s", u_data["role"], u_data["email"])
            user = UserModel(
                id=u_data["id"],
                tenant_id=t_data["id"],
                email=u_data["email"],
                hashed_password=get_password_hash(u_data["password"]),
                full_name=u_data["full_name"],
                role=u_data["role"],
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            session.add(user)
            email_to_user_id[u_data["email"]] = u_data["id"]
    await session.flush()

    # 3. Accounts
    for t_data in TENANTS:
        slug = t_data["slug"]
        for a_data in ACCOUNTS[slug]:
            owner_id = email_to_user_id[a_data["owner_key"]]
            log.info(
                "  → Cuenta [%s] %s %s — %s",
                a_data["status"], a_data["account_number"],
                a_data["currency"], a_data["balance"],
            )
            account = AccountModel(
                id=a_data["id"],
                account_number=a_data["account_number"],
                owner_id=owner_id,
                tenant_id=t_data["id"],
                balance=a_data["balance"],
                currency=a_data["currency"],
                status=a_data["status"],
                created_at=now,
                updated_at=now,
            )
            session.add(account)
    await session.flush()

    await session.commit()
    log.info("\n✅ Seed completado exitosamente.")
    _print_summary()


def _print_summary() -> None:
    print("\n" + "=" * 70)
    print("  BANKOS DEMO — Credenciales de Acceso")
    print("=" * 70)
    print("\n[ TENANT 1 ] banco-andino")
    print("  POST /api/v1/auth/login → { tenant_slug: 'banco-andino', ... }")
    print("  Admin  : admin@banco-andino.com   / Admin123!")
    print("  Cliente: juan@banco-andino.com    / Cliente123! → ctas 1000000001 (COP), 1000000002 (USD)")
    print("  Cliente: jaysir@banco-andino.com  / Cliente123! → ctas 1000000003 (COP), 1000000004 (EUR)")
    print("  Comisión: 1.5% sobre transferencias")
    print("\n[ TENANT 2 ] cooperativa-caribe")
    print("  POST /api/v1/auth/login → { tenant_slug: 'cooperativa-caribe', ... }")
    print("  Admin  : admin@coop-caribe.com    / Admin123!")
    print("  Cliente: cliente1@coop-caribe.com / Cliente123! → ctas 2000000001 (USD), 2000000003 (COP)")
    print("  Cliente: cliente2@coop-caribe.com / Cliente123! → ctas 2000000002 (USD)")
    print("  Comisión: FIJA 2.5 USD")
    print("\n  Swagger UI: http://localhost:8000/api/v1/docs")
    print("=" * 70 + "\n")


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------

async def main() -> None:
    log.info("Conectando a la base de datos: %s", settings.async_database_url.split("@")[-1])

    engine = create_async_engine(settings.async_database_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        log.info("Tablas verificadas/creadas.")

    async with session_factory() as session:
        try:
            await seed(session)
        except Exception as exc:
            await session.rollback()
            log.error("Error durante el seed: %s", exc)
            raise

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

