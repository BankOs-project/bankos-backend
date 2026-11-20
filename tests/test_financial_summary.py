"""Casos del dashboard con saldos actuales y movimientos en PostgreSQL."""

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
import unittest
from unittest.mock import patch
import uuid

from fastapi.testclient import TestClient

from demo_seed_fixture import load_demo_seed


API = "/api/v1"
START = datetime(2026, 11, 1, 5, tzinfo=timezone.utc)
END = datetime(2026, 11, 3, 5, tzinfo=timezone.utc)
COP = "3200000001"
USD = "3200000002"
COUNTERPARTY = "3200000003"
BLOCKED = "3999000099"


@unittest.skipUnless(
    os.getenv("BANKOS_TEST_DATABASE_URL") and os.getenv("BANKOS_TEST_REDIS_URL"),
    "Requiere PostgreSQL y Redis desechables",
)
class FinancialSummaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        stack = ExitStack()
        cls.addClassCleanup(stack.close)
        stack.enter_context(patch.dict(os.environ, {
            "DATABASE_URL": os.environ["BANKOS_TEST_DATABASE_URL"],
            "REDIS_URL": os.environ["BANKOS_TEST_REDIS_URL"],
            "ENVIRONMENT": "test",
            "DEBUG": "false",
        }))

        from app.main import app
        from scripts.seed_demo import USERS

        cls.client = stack.enter_context(TestClient(app))
        cls.client.portal.call(load_demo_seed)
        user = USERS["nu"][0]
        login = cls.client.post(f"{API}/auth/login", json={
            "tenant_slug": "nu", "email": user["email"], "password": user["password"],
        })
        if login.status_code != 200:
            raise AssertionError(login.text)
        identity = login.json()["data"]
        cls.headers = {"Authorization": f"Bearer {identity['access_token']}"}
        cls.client.portal.call(cls.insert_fixtures, uuid.UUID(identity["tenant_id"]), uuid.UUID(identity["user_id"]))

    @staticmethod
    async def insert_fixtures(tenant_id, owner_id):
        from app.core.database import async_session_factory
        from app.modules.accounts.infrastructure.models import AccountModel
        from app.modules.transactions.infrastructure.models import TransactionModel
        from scripts.seed_demo import TENANTS

        foreign_tenant = TENANTS[0]["id"]
        middle = START + timedelta(hours=1)

        def movement(kind, source, target, amount, source_currency, converted, target_currency, **overrides):
            return TransactionModel(**{
                "tenant_id": tenant_id, "operation_type": kind,
                "source_account_number": source, "destination_account_number": target,
                "original_amount": Decimal(amount), "original_currency": source_currency,
                "converted_amount": Decimal(converted), "destination_currency": target_currency,
                "status": "SUCCESS", "fee_charged": Decimal("0"), "created_at": middle,
                "idempotency_key": str(uuid.uuid4()), "correlation_id": str(uuid.uuid4()),
                **overrides,
            })

        records = [
            movement("DEPOSIT", None, COP, "120", "COP", "120", "COP", created_at=START),
            movement("WITHDRAWAL", COP, None, "20", "COP", "20", "COP", created_at=END),
            movement("DEPOSIT", None, USD, "40", "USD", "40", "USD"),
            movement("TRANSFER", COP, COUNTERPARTY, "100", "COP", "100", "COP", fee_charged=Decimal("1")),
            movement("TRANSFER", COUNTERPARTY, USD, "123000", "COP", "30", "USD", fee_charged=Decimal("10")),
            movement("TRANSFER", USD, COP, "10", "USD", "41000", "COP", fee_charged=Decimal("0.10")),
            movement("TRANSFER", USD, BLOCKED, "5", "USD", "5", "USD", fee_charged=Decimal("0.05")),
            movement("DEPOSIT", None, BLOCKED, "3", "USD", "3", "USD"),
            movement("DEPOSIT", None, COP, "900", "COP", "900", "COP", status="ERROR"),
            movement("DEPOSIT", None, COP, "900", "COP", "900", "COP", tenant_id=foreign_tenant),
            movement("DEPOSIT", None, COP, "900", "COP", "900", "COP", created_at=START - timedelta(microseconds=1)),
            movement("DEPOSIT", None, COP, "900", "COP", "900", "COP", created_at=END + timedelta(microseconds=1)),
        ]
        records.extend(movement("DEPOSIT", None, COP, "1", "COP", "1", "COP") for _ in range(105))

        async with async_session_factory.begin() as session:
            session.add(AccountModel(
                account_number=BLOCKED, owner_id=owner_id, tenant_id=tenant_id,
                balance=Decimal("9000"), currency="USD", status="BLOCKED",
            ))
            session.add_all(records)

    def summary(self, start=START.isoformat(), end=END.isoformat(), **filters):
        return self.client.get(f"{API}/transactions/summary", headers=self.headers, params={
            "start_date": start, "end_date": end, **filters,
        })

    def totals(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["internal_code"], "FINANCIAL_SUMMARY_FETCHED")
        data = response.json()["data"]
        summary = {}
        for currency in data["currencies"]:
            fields = [currency[name] for name in ("available_balance", "income", "expenses")]
            self.assertTrue(all(isinstance(value, str) for value in fields))
            summary[currency["currency"]] = tuple(map(Decimal, fields))
        return data, summary

    def test_full_period_aggregates_all_confirmed_rows_per_currency(self):
        data, totals = self.totals(self.summary(start="2026-11-01T00:00:00-05:00"))
        self.assertIsNone(data["account_number"])
        self.assertEqual(data["start_date"], "2026-11-01T05:00:00Z")
        self.assertEqual(data["end_date"], "2026-11-03T05:00:00Z")
        self.assertEqual(totals, {"COP": (425000, 225, 121), "USD": (80, 73, Decimal("0.15"))})

    def test_account_scope_counts_only_its_side_of_transfers(self):
        data, cop = self.totals(self.summary(account_number=COP))
        self.assertEqual(data["account_number"], COP)
        self.assertEqual(cop, {"COP": (425000, 41225, 121)})
        self.assertEqual(self.totals(self.summary(account_number=USD))[1], {"USD": (80, 70, Decimal("15.15"))})
        self.assertEqual(self.totals(self.summary(account_number=BLOCKED))[1], {"USD": (0, 8, 0)})

    def test_inclusive_instants_and_empty_range(self):
        for instant, expected in ((START, (425000, 120, 0)), (END, (425000, 0, 20))):
            with self.subTest(instant=instant):
                _, totals = self.totals(self.summary(start=instant.isoformat(), end=instant.isoformat()))
                self.assertEqual(totals["COP"], expected)
        _, empty = self.totals(self.summary(start="2026-11-04T00:00:00Z", end="2026-11-04T12:00:00Z"))
        self.assertEqual(empty, {"COP": (425000, 0, 0), "USD": (80, 0, 0)})

    def test_foreign_account_and_invalid_ranges(self):
        for outsider in (COUNTERPARTY, "1000000001", "0000000000"):
            with self.subTest(outsider=outsider):
                result = self.summary(account_number=outsider)
                self.assertEqual(result.status_code, 403, result.text)
                self.assertEqual(result.json()["internal_code"], "AUTHORIZATION_FAILED")
        inverted = self.summary(start=END.isoformat(), end=START.isoformat())
        self.assertEqual(inverted.status_code, 422, inverted.text)
        self.assertEqual(inverted.json()["internal_code"], "INVALID_DATE_RANGE")
        for bad_date in ("2026-11-01", "2026-11-01T05:00:00"):
            missing_zone = self.summary(start=bad_date)
            self.assertEqual(missing_zone.status_code, 422, missing_zone.text)
            self.assertEqual(missing_zone.json()["internal_code"], "VALIDATION_ERROR")
