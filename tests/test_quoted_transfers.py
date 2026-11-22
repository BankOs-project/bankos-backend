"""Transferencias simuladas, proyecciones por tenant y reintentos durables."""

from contextlib import ExitStack
from decimal import Decimal
import os
import unittest
from unittest.mock import patch
import uuid

from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import text

from demo_seed_fixture import load_demo_seed
from test_transaction_idempotency import DropResponse


API = "/api/v1"
SAME_NUMBER = "3200000002"


@unittest.skipUnless(
    os.getenv("BANKOS_TEST_DATABASE_URL") and os.getenv("BANKOS_TEST_REDIS_URL"),
    "Requiere PostgreSQL y Redis desechables",
)
class QuotedTransfersTest(unittest.TestCase):
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
        from app.core.config import settings
        from app.core.database import async_session_factory
        from app.main import app
        from app.modules.accounts.infrastructure.models import AccountModel

        cls.settings = settings
        cls.sessions = async_session_factory
        stack.enter_context(patch.object(settings, "SIMULATED_INTERBANK_ENABLED", True))
        cls.transport = DropResponse(app)
        cls.client = stack.enter_context(TestClient(cls.transport, raise_server_exceptions=False))
        cls.client.portal.call(load_demo_seed)
        cls.actors = {}
        for label, slug, index in (
            ("sender", "nu", 1), ("recipient", "daviplata", 2),
            ("invalid_lookup", "falabella", 1), ("other_in_source", "nu", 2),
        ):
            response = cls.client.post(f"{API}/auth/login", headers={"X-Test-Peer": "198.51.100.52"}, json={
                "tenant_slug": slug, "email": f"cliente{index}.{slug}@example.com", "password": "BankOSDemo123!",
            })
            if response.status_code != 200:
                raise AssertionError(response.text)
            cls.actors[label] = response.json()["data"]
        banks = cls.client.get(f"{API}/banks").json()["data"]
        cls.bank = next(bank["bank_id"] for bank in banks if bank["name"] == "DaviPlata")

        async def duplicate_account_number():
            recipient = cls.actors["recipient"]
            async with cls.sessions.begin() as session:
                session.add(AccountModel(
                    account_number=SAME_NUMBER, owner_id=uuid.UUID(recipient["user_id"]),
                    tenant_id=uuid.UUID(recipient["tenant_id"]), balance=Decimal("9"),
                    currency="COP", status="ACTIVE",
                ))

        cls.client.portal.call(duplicate_account_number)

    def headers(self, actor):
        return {
            "Authorization": f"Bearer {self.actors[actor]['access_token']}",
            "X-Test-Peer": "198.51.100.53",
        }

    def check(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.json()["internal_code"], code)
        return response.json()["data"]

    def quote(self, *, actor="sender", **changes):
        return self.client.post(f"{API}/transactions/quote", headers=self.headers(actor), json={
            "operation_type": "TRANSFER", "transfer_mode": "OTHER_BANK",
            "source_account_number": SAME_NUMBER, "destination_account_number": SAME_NUMBER,
            "destination_bank_id": self.bank, "amount": "10", **changes,
        })

    def confirm(self, quote_id, key=None, *, actor="sender", drop=False):
        headers = {
            **self.headers(actor), "Idempotency-Key": key or str(uuid.uuid4()),
            "X-Correlation-ID": "quoted-transfer-test",
        }
        if drop:
            headers["X-Test-Drop-Response"] = "1"
        return self.client.post(f"{API}/transactions/transfer", headers=headers, json={"quote_id": quote_id})

    async def balances(self):
        async with self.sessions() as session:
            result = await session.execute(text("""
                SELECT tenant_id, balance FROM accounts WHERE account_number = :number
            """), {"number": SAME_NUMBER})
            return {str(row.tenant_id): row.balance for row in result}

    async def transfer_rows(self):
        async with self.sessions() as session:
            return await session.scalar(text("SELECT count(*) FROM transactions WHERE transfer_mode = 'OTHER_BANK'"))

    def test_complete_transfer_lost_response_and_isolated_history(self):
        sender, recipient = self.actors["sender"], self.actors["recipient"]
        before = self.client.portal.call(self.balances)
        row_count = self.client.portal.call(self.transfer_rows)
        quote = self.check(self.quote(), 200, "QUOTE_CALCULATED")
        self.assertEqual(quote["recipient"], {
            "bank_name": "DaviPlata", "masked_holder_name": "N*** P*** D***", "account_last_four": "0002",
        })
        self.assertEqual(Decimal(quote["fee_calculated"]), Decimal("0.10"))
        self.assertEqual(Decimal(quote["amount_to_debit"]), Decimal("10.10"))
        self.assertEqual(Decimal(quote["exchange_rate_applied"]), Decimal("4100"))
        self.assertEqual(Decimal(quote["final_amount_to_receive"]), Decimal("41000"))
        for actor in ("recipient", "other_in_source"):
            self.check(self.confirm(quote["quote_id"], actor=actor), 404, "QUOTE_UNAVAILABLE")

        key = str(uuid.uuid4())
        lost = self.confirm(quote["quote_id"], key, drop=True)
        self.assertEqual(lost.content, b"")
        start = next(m for m in self.transport.messages if m["type"] == "http.response.start")
        body = b"".join(m.get("body", b"") for m in self.transport.messages if m["type"] == "http.response.body")
        self.assertEqual(start["status"], 201)

        async def lose_cache():
            async with Redis.from_url(os.environ["BANKOS_TEST_REDIS_URL"]) as redis:
                await redis.delete(f"idemp:receipt:v1:{sender['tenant_id']}:{sender['user_id']}:{key}")

        self.client.portal.call(lose_cache)
        replay = self.confirm(quote["quote_id"], key)
        outgoing = self.check(replay, 201, "TRANSFER_SUCCESS")
        self.assertEqual(replay.content, body)
        self.assertEqual(replay.headers["X-Correlation-ID"], "quoted-transfer-test")
        self.check(self.confirm(quote["quote_id"]), 409, "QUOTE_ALREADY_USED")
        balances = self.client.portal.call(self.balances)
        self.assertEqual(before[sender["tenant_id"]] - balances[sender["tenant_id"]], Decimal("10.10"))
        self.assertEqual(balances[recipient["tenant_id"]] - before[recipient["tenant_id"]], Decimal("41000"))
        self.assertEqual(self.client.portal.call(self.transfer_rows) - row_count, 2)
        self.assertEqual(Decimal(outgoing["fee_charged"]), Decimal(quote["fee_calculated"]))
        self.assertEqual(Decimal(outgoing["converted_amount"]), Decimal(quote["final_amount_to_receive"]))
        self.assertIsNone(outgoing["destination_account_number"])
        for actor, local, remote in (("sender", sender, recipient), ("recipient", recipient, sender)):
            response = self.client.get(f"{API}/transactions", headers=self.headers(actor))
            history = self.check(response, 200, "TRANSACTIONS_FETCHED")
            record = next(i for i in history["items"] if i["transfer_reference"] == outgoing["transfer_reference"])
            self.assertEqual(record["tenant_id"], local["tenant_id"])
            self.assertNotIn(remote["tenant_id"], str(record))
            self.assertNotIn(remote["user_id"], str(record))
            if actor == "recipient":
                self.assertIsNone(record["source_account_number"])
                self.assertEqual(record["original_currency"], "COP")
                self.assertEqual(Decimal(record["original_amount"]), Decimal("41000"))
                self.assertEqual(Decimal(record["fee_charged"]), 0)
                self.assertNotEqual(record["idempotency_key"], key)
        self.check(self.client.get(
            f"{API}/accounts/3300000003", headers=self.headers("sender"),
        ), 404, "RESOURCE_NOT_FOUND")

    def test_same_bank_zero_and_failed_second_record_is_atomic(self):
        same = self.check(self.quote(
            transfer_mode="SAME_BANK", destination_bank_id=None, destination_account_number="3200000001",
        ), 200, "QUOTE_CALCULATED")
        self.assertEqual(Decimal(same["fee_calculated"]), 0)
        sent = self.check(self.confirm(same["quote_id"]), 201, "TRANSFER_SUCCESS")
        self.assertEqual(Decimal(sent["fee_charged"]), 0)
        self.assertEqual(Decimal(sent["converted_amount"]), Decimal(same["final_amount_to_receive"]))

        from app.modules.transactions.infrastructure.repositories import PostgresTransactionRepository

        quote = self.check(self.quote(), 200, "QUOTE_CALCULATED")
        before = self.client.portal.call(self.balances)
        count = self.client.portal.call(self.transfer_rows)
        original = PostgresTransactionRepository.create

        async def fail_credit_record(repository, transaction):
            if transaction.transfer_mode and transaction.source_account_number is None:
                raise RuntimeError("Fallo de prueba al persistir el abono")
            return await original(repository, transaction)

        key = str(uuid.uuid4())
        with patch.object(PostgresTransactionRepository, "create", fail_credit_record):
            with self.assertLogs("bankos.exceptions", level="ERROR"):
                self.check(self.confirm(quote["quote_id"], key), 500, "INTERNAL_SERVER_ERROR")
        self.assertEqual(self.client.portal.call(self.balances), before)
        self.assertEqual(self.client.portal.call(self.transfer_rows), count)
        self.check(self.confirm(quote["quote_id"], key), 201, "TRANSFER_SUCCESS")

    def test_invalid_destinations_share_error_and_rate_limit(self):
        requests = [
            {"destination_bank_id": str(uuid.uuid4())},
            {"destination_account_number": "0000000000"},
            {"destination_account_number": "3300000004"},
        ]
        for changes in requests + [requests[1]] * 5:
            response = self.quote(actor="invalid_lookup", source_account_number="3500000002", **changes)
            self.assertEqual(response.status_code, 404, response.text)
            self.assertEqual(response.json(), {
                "success": False, "internal_code": "DESTINATION_UNAVAILABLE",
                "message": "El destino no está disponible para recibir la transferencia", "data": None,
            })
        limited = self.quote(actor="invalid_lookup", source_account_number="3500000002")
        self.check(limited, 429, "RATE_LIMIT_EXCEEDED")
        self.assertIn("Retry-After", limited.headers)

    def test_expiry_changed_terms_and_feature_flag(self):
        with patch.object(self.settings, "SIMULATED_INTERBANK_ENABLED", False):
            self.check(self.quote(), 403, "INTERBANK_DISABLED")
        expired = self.check(self.quote(), 200, "QUOTE_CALCULATED")

        async def expire():
            async with self.sessions.begin() as session:
                await session.execute(text("UPDATE transfer_quotes SET expires_at = now() WHERE id = :id"),
                                      {"id": uuid.UUID(expired["quote_id"])})

        self.client.portal.call(expire)
        self.check(self.confirm(expired["quote_id"]), 409, "QUOTE_EXPIRED")
        stale = self.check(self.quote(), 200, "QUOTE_CALCULATED")

        async def set_fee(fee):
            async with self.sessions.begin() as session:
                await session.execute(text("UPDATE tenants SET fee_value = :fee WHERE slug = 'nu'"), {"fee": fee})

        before = self.client.portal.call(self.balances)
        self.client.portal.call(set_fee, Decimal("2"))
        try:
            self.check(self.confirm(stale["quote_id"]), 409, "QUOTE_STALE")
        finally:
            self.client.portal.call(set_fee, Decimal("1"))
        self.check(self.confirm(stale["quote_id"]), 409, "QUOTE_STALE")
        self.assertEqual(self.client.portal.call(self.balances), before)
