"""Carga explícitamente los datos ficticios en una base de pruebas aprovisionada."""
from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
import logging
import sys
import os
import uuid

# Asegurar que el directorio raíz del proyecto esté en PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory, engine
from app.core.security import get_password_hash
from app.modules.tenants.application.dtos import public_logo_url

from app.modules.tenants.infrastructure.models import TenantModel
from app.modules.auth_users.infrastructure.models import PersonModel, UserModel
from app.modules.accounts.infrastructure.models import AccountModel

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


DEMO_PASSWORD = "BankOSDemo123!"
NEW_BANKS = (
    # Slug, nombre, prefijo de cuenta, clientes, saldos COP/USD/COP, tasa USD a COP.
    ("bancolombia", "Bancolombia", "31", "Lina Prado Demo", "Teo Rojas Demo", 850000, 240, 25, 4200),
    ("nu", "Nu", "32", "Alma Ríos Demo", "Bruno Mora Demo", 425000, 80, 12, 4100),
    ("daviplata", "DaviPlata", "33", "Vera Sol Demo", "Nico Paz Demo", 1230000, 125, 5, 4050),
    ("uno", "Uno", "34", "Sara Valle Demo", "Iván Lago Demo", 185000, 42, 50, 4300),
    ("falabella", "Falabella", "35", "Noa Palma Demo", "Elías Nube Demo", 2100000, 350, 15, 4150),
)
SEED_LOCK_ID = 0x42616E6B4F53


def _demo_uuid(kind: str, slug: str, value: str = "") -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"https://bankos.example/demo/{kind}/{slug}/{value}")


for slug, name, prefix, first_name, second_name, cop, usd, small_balance, usd_to_cop in NEW_BANKS:
    TENANTS.append({
        "id": _demo_uuid("bank", slug),
        "name": name,
        "slug": slug,
        "max_transaction_limit": Decimal("50000000"),
        "fee_type": "PERCENTAGE",
        "fee_value": Decimal("1"),
        "base_currency": "COP",
        "exchange_rates": {"USD_COP": str(usd_to_cop)},
        "webhook_url": None,
    })

    USERS[slug] = [
        {
            "id": _demo_uuid("user", slug, str(number)),
            "email": f"cliente{number}.{slug}@example.com",
            "password": DEMO_PASSWORD,
            "full_name": full_name,
            "role": "cliente",
        }
        for number, full_name in enumerate((first_name, second_name), start=1)
    ]
    account_cases = (
        (0, cop, "COP", "ACTIVE"),
        (0, usd, "USD", "ACTIVE"),
        (1, small_balance, "COP", "ACTIVE"),
        (1, 0, "COP", "INACTIVE"),
    )
    ACCOUNTS[slug] = [
        {
            "id": _demo_uuid("account", slug, str(number)),
            "account_number": f"{prefix}{number:08d}",
            "owner_key": USERS[slug][owner]["email"],
            "balance": Decimal(balance),
            "currency": currency,
            "status": status,
        }
        for number, (owner, balance, currency, status) in enumerate(account_cases, start=1)
    ]


async def seed(session: AsyncSession, logos: Mapping[str, str] | None = None) -> dict[str, int]:
    """Agrega filas faltantes y carga logos explícitos únicamente si falta el logo."""
    await session.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": SEED_LOCK_ID})
    counts = {"banks": 0, "users": 0, "accounts": 0, "logos": 0}
    now = datetime.now(timezone.utc)

    for bank_data in TENANTS:
        slug = bank_data["slug"]
        logo_url = public_logo_url((logos or {}).get(slug))
        bank = await session.scalar(select(TenantModel).where(TenantModel.slug == slug))
        if bank is None:
            bank = TenantModel(
                **bank_data,
                logo_url=logo_url,
                is_active=True,
                directory_enabled=True,
                created_at=now,
                updated_at=now,
            )
            session.add(bank)
            await session.flush()
            counts["banks"] += 1
        elif logo_url is not None and bank.logo_url is None:
            bank.logo_url = logo_url
            counts["logos"] += 1

        users = USERS[slug]
        existing_users = await session.execute(
            select(UserModel.email, UserModel.id).where(
                UserModel.tenant_id == bank.id,
                UserModel.email.in_([user["email"] for user in users]),
            )
        )
        owners = {email: user_id for email, user_id in existing_users}
        for user in users:
            email = user["email"]
            if email in owners:
                continue
            person = PersonModel(id=uuid.uuid4())
            session.add(person)
            await session.flush()
            session.add(UserModel(
                id=user["id"],
                tenant_id=bank.id,
                person_id=person.id,
                email=email,
                hashed_password=get_password_hash(user["password"]),
                full_name=user["full_name"],
                role=user["role"],
                is_active=True,
                created_at=now,
                updated_at=now,
            ))
            owners[email] = user["id"]
            counts["users"] += 1
        await session.flush()

        accounts = ACCOUNTS[slug]
        existing_numbers = set(await session.scalars(
            select(AccountModel.account_number).where(
                AccountModel.tenant_id == bank.id,
                AccountModel.account_number.in_([account["account_number"] for account in accounts]),
            )
        ))
        for account in accounts:
            if account["account_number"] in existing_numbers:
                continue
            session.add(AccountModel(
                id=account["id"],
                tenant_id=bank.id,
                account_number=account["account_number"],
                owner_id=owners[account["owner_key"]],
                balance=account["balance"],
                currency=account["currency"],
                status=account["status"],
                created_at=now,
                updated_at=now,
            ))
            counts["accounts"] += 1

    await session.commit()
    return counts


def _parse_logo(value: str) -> tuple[str, str]:
    slug, separator, raw_url = value.partition("=")
    logo_url = public_logo_url(raw_url)
    if not separator or not logo_url or slug not in USERS:
        raise argparse.ArgumentTypeError("Usa --logo SLUG_DEMO=URL_HTTPS sin credenciales en la URL")
    return slug, logo_url


async def main(logos: Mapping[str, str]) -> None:
    log.info("Base de pruebas elegida: host=%s base=%s", engine.url.host, engine.url.database)
    try:
        async with async_session_factory() as session:
            counts = await seed(session, logos)
        log.info(
            "Datos agregados: %d bancos, %d usuarios, %d cuentas y %d logos de bancos existentes.",
            counts["banks"], counts["users"], counts["accounts"], counts["logos"],
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", required=True, help="Confirma la carga en la base configurada")
    parser.add_argument("--logo", action="append", type=_parse_logo, default=[], metavar="SLUG=URL")
    args = parser.parse_args()
    asyncio.run(main(dict(args.logo)))

