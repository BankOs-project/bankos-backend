"""Integración de API y permisos con el rol desechable bankos_app."""

from contextlib import ExitStack, redirect_stdout
from decimal import Decimal
from io import StringIO
import os
import unittest
from unittest.mock import patch
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


API = "/api/v1"
BANK_A = "banco-andino"
BANK_B = "cooperativa-caribe"
SOURCE = "1000000002"
DESTINATION = "1000000003"
FOREIGN = "2000000001"


@unittest.skipUnless(
    os.getenv("BANKOS_TEST_DATABASE_URL") and os.getenv("BANKOS_TEST_REDIS_URL")
    and os.getenv("DATABASE_MANAGED_SCHEMA", "").lower() == "true",
    "Requiere PostgreSQL desechable preparado con el rol bankos_app",
)
class RateLimitsAndAuditTest(unittest.TestCase):
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
        from app.modules.transactions.infrastructure.repositories import PostgresTransactionRepository
        from scripts.seed_demo import USERS

        stack.enter_context(patch.object(settings, "RATE_LIMIT_TRUSTED_PROXIES", "198.51.100.0/24"))
        stack.enter_context(patch.object(app, "middleware_stack", None))
        cls.session_factory = async_session_factory
        cls.transaction_repository = PostgresTransactionRepository

        async def with_client_ip(scope, receive, send):
            illustrative_ip = dict(scope.get("headers", [])).get(b"x-test-peer")
            if illustrative_ip:
                scope = {**scope, "client": (illustrative_ip.decode(), 1234)}
            await app(scope, receive, send)

        with redirect_stdout(StringIO()):
            cls.client = stack.enter_context(TestClient(with_client_ip, raise_server_exceptions=False))

        cls.sessions = {}
        for label, tenant, index in (
            ("admin_a", BANK_A, 0), ("admin_b", BANK_B, 0),
            ("client_a", BANK_A, 1), ("client_b", BANK_A, 2),
        ):
            user = USERS[tenant][index]
            result = cls.client.post(f"{API}/auth/login", json={
                "tenant_slug": tenant, "email": user["email"], "password": user["password"],
            }, headers={"X-Test-Peer": "192.0.2.1"})
            if result.status_code != 200:
                raise AssertionError(result.text)
            cls.sessions[label] = result.json()["data"]

    def request(self, method, path, *, actor=None, peer="192.0.2.2", **kwargs):
        headers = {"X-Test-Peer": peer, **kwargs.pop("headers", {})}
        if actor:
            headers["Authorization"] = f"Bearer {self.sessions[actor]['access_token']}"
        return self.client.request(method, f"{API}{path}", headers=headers, **kwargs)

    def check(self, result, status, code):
        self.assertEqual(result.status_code, status, result.text)
        self.assertEqual(result.json()["internal_code"], code)
        return result.json()["data"]

    def limited(self, result, limit):
        data = self.check(result, 429, "RATE_LIMIT_EXCEEDED")
        self.assertEqual(data["limit"], limit)
        self.assertEqual(int(result.headers["Retry-After"]), data["retry_after_seconds"])
        self.assertIn(data["retry_after_seconds"], range(1, 61))
        self.assertIn("X-Correlation-ID", result.headers)

    def test_login_account_and_ip(self):
        credentials = {
            "tenant_slug": BANK_B, "email": "cliente2@coop-caribe.com", "password": "incorrecta",
        }
        for number in range(10, 15):
            response = self.request("POST", "/auth/login", peer=f"192.0.2.{number}", json=credentials)
            self.check(response, 401, "AUTHENTICATION_FAILED")
        normalized = {**credentials, "email": credentials["email"].upper(), "tenant_slug": f" {BANK_B.upper()} "}
        self.limited(self.request("POST", "/auth/login", peer="192.0.2.15", json=normalized), 5)
        separate_tenant = {**credentials, "tenant_slug": BANK_A}
        self.check(self.request("POST", "/auth/login", peer="192.0.2.16", json=separate_tenant), 401, "AUTHENTICATION_FAILED")

        for count in range(10):
            response = self.request("POST", "/auth/login", peer="192.0.2.20", json={})
            self.check(response, 422, "VALIDATION_ERROR")
        self.limited(self.request(
            "POST", "/auth/login", peer="192.0.2.20", json={},
            headers={"X-Forwarded-For": "192.0.2.99", "X-Real-IP": "192.0.2.99"},
        ), 10)
        self.check(self.request("POST", "/auth/login", peer="192.0.2.21", json={}), 422, "VALIDATION_ERROR")
        for _ in range(10):
            self.check(self.request(
                "POST", "/auth/login", peer="198.51.100.1", json={}, headers={"X-Real-IP": "203.0.113.20"},
            ), 422, "VALIDATION_ERROR")
        self.limited(self.request(
            "POST", "/auth/login", peer="198.51.100.2", json={}, headers={"X-Real-IP": "203.0.113.20"},
        ), 10)
        self.check(self.request(
            "POST", "/auth/login", peer="198.51.100.2", json={}, headers={"X-Real-IP": "203.0.113.21"},
        ), 422, "VALIDATION_ERROR")

    def test_transaction_user_and_ip(self):
        email = f"rate-limit-{uuid.uuid4().hex}@example.com"
        password = "FixtureRate123!"
        self.check(self.request("POST", "/auth/users", actor="admin_b", json={
            "email": email, "password": password, "full_name": "Administrador de prueba", "role": "administrador",
        }), 201, "USER_CREATED")
        self.sessions["rate_actor"] = self.check(self.request("POST", "/auth/login", peer="192.0.2.34", json={
            "tenant_slug": BANK_B, "email": email, "password": password,
        }), 200, "LOGIN_SUCCESS")
        for number in range(60):
            peer = f"192.0.2.{30 + number // 20}"
            if number % 2:
                response = self.request("GET", "/transactions", actor="rate_actor", peer=peer)
                self.check(response, 200, "TRANSACTIONS_FETCHED")
            else:
                response = self.request("POST", "/transactions/quote", actor="rate_actor", peer=peer, json={
                    "operation_type": "DEPOSIT", "source_account_number": FOREIGN, "amount": "1",
                })
                self.check(response, 200, "QUOTE_CALCULATED")
        self.limited(self.request("GET", "/transactions", actor="rate_actor", peer="192.0.2.33"), 60)
        self.check(self.request("GET", "/transactions", actor="admin_a", peer="192.0.2.33"), 200, "TRANSACTIONS_FETCHED")
        self.check(self.request("GET", "/transactions", peer="192.0.2.33", headers={
            "Authorization": "Bearer invalid",
        }), 401, "UNAUTHORIZED")

        for _ in range(30):
            self.check(self.request("GET", "/transactions", actor="admin_a", peer="192.0.2.40"), 200, "TRANSACTIONS_FETCHED")
        self.limited(self.request(
            "POST", "/transactions/quote", peer="192.0.2.40", json={}, actor="client_b",
        ), 30)

    def test_administrative_records_and_database_permissions(self):
        actor = self.sessions["admin_a"]["user_id"]
        user = self.check(self.request("POST", "/auth/users", actor="admin_a", json={
            "email": f"auditoria-{uuid.uuid4().hex}@example.com", "password": "Fixture123!",
            "full_name": "Cliente de auditoría", "role": "cliente",
        }), 201, "USER_CREATED")
        account = self.check(self.request("POST", "/accounts", actor="admin_a", json={
            "owner_id": user["id"], "currency": "COP",
        }), 201, "ACCOUNT_CREATED")
        self.check(self.request(
            "PATCH", f"/accounts/{account['account_number']}/deactivate", actor="admin_a",
        ), 200, "ACCOUNT_DEACTIVATED")
        self.check(self.request(
            "PATCH", "/tenants/config", actor="admin_a", json={"max_transaction_limit": "125000"},
        ), 200, "TENANT_CONFIG_UPDATED")
        foreign_tenant = self.sessions["admin_b"]["tenant_id"]
        self.check(self.request(
            "PATCH", f"/tenants/{foreign_tenant}/config", actor="admin_a",
            json={"max_transaction_limit": "1"},
        ), 403, "TENANT_ACCESS_DENIED")

        entries = self.check(self.request("GET", "/audit-logs", actor="admin_a"), 200, "AUDIT_LOGS_FETCHED")["items"]
        self.assertEqual([e["action"] for e in entries[:4]], [
            "TENANT_CONFIG_UPDATED", "ACCOUNT_DEACTIVATED", "ACCOUNT_OPENED", "USER_CREATED",
        ])
        for entry in entries[:4]:
            self.assertEqual(entry["user_id"], actor)
            self.assertEqual(entry["tenant_id"], self.sessions["admin_a"]["tenant_id"])
            self.assertTrue(entry["created_at"])
        self.assertNotEqual(entries[0]["previous_state"]["max_transaction_limit"], "125000")
        self.assertEqual(Decimal(entries[0]["new_state"]["max_transaction_limit"]), Decimal("125000"))
        self.assertEqual(entries[1]["previous_state"]["status"], "ACTIVE")
        self.assertEqual(entries[1]["new_state"]["status"], "INACTIVE")
        self.assertIsNone(entries[2]["previous_state"])
        self.assertEqual(entries[2]["new_state"]["account_number"], account["account_number"])
        self.assertIsNone(entries[3]["previous_state"])
        self.assertEqual(entries[3]["new_state"]["user_id"], user["id"])
        self.assertNotIn("hashed_password", entries[3]["new_state"])
        self.assertEqual(self.check(self.request("GET", "/audit-logs", actor="admin_b"), 200, "AUDIT_LOGS_FETCHED")["total"], 0)
        self.check(self.request("GET", "/audit-logs", actor="client_a"), 403, "AUTHORIZATION_FAILED")

        async def verify_role():
            async with self.session_factory() as session:
                self.assertEqual(await session.scalar(text("SELECT current_user")), "bankos_app")
            for statement in (
                "UPDATE public.audit_logs SET action = action",
                "DELETE FROM public.audit_logs",
                "TRUNCATE public.audit_logs",
            ):
                async with self.session_factory() as session:
                    with self.assertRaises(DBAPIError) as denied:
                        await session.execute(text(statement))
                    self.assertEqual(denied.exception.orig.sqlstate, "42501")

        self.client.portal.call(verify_role)

    def test_rejected_operations_are_persisted_and_private(self):
        from app.core.exceptions import BankOSException

        async def state():
            async with self.session_factory() as session:
                balances = (await session.execute(text("""
                    SELECT account_number, balance FROM accounts ORDER BY account_number
                """))).all()
                count = await session.scalar(text("SELECT count(*) FROM transaction_idempotency_receipts"))
                return balances, count

        baseline = self.client.portal.call(state)
        cases = (
            ("deposit", {"account_number": "1000000005", "amount": "10"}, 400, "ACCOUNT_INACTIVE"),
            ("withdraw", {"account_number": SOURCE, "amount": "10000000000"}, 422, "INSUFFICIENT_FUNDS"),
            ("transfer", {
                "source_account_number": SOURCE, "destination_account_number": FOREIGN, "amount": "10",
            }, 404, "RESOURCE_NOT_FOUND"),
        )
        attempts = {}

        def reject(operation, body, status, code):
            key = str(uuid.uuid4())
            correlation = str(uuid.uuid4())
            response = self.request("POST", f"/transactions/{operation}", actor="client_a", json=body, headers={
                "Idempotency-Key": key, "X-Correlation-ID": correlation,
            })
            self.check(response, status, code)
            self.assertEqual(response.headers["X-Correlation-ID"], correlation)
            attempts[key] = (correlation, code, Decimal(body["amount"]))

        for case in cases:
            reject(*case)

        create = self.transaction_repository.create

        async def reject_after_writes(repository, transaction):
            if transaction.status.value == "SUCCESS":
                raise BankOSException("Rechazo de prueba después de actualizar ambos saldos", "TEST_REJECTION", 422)
            return await create(repository, transaction)

        with patch.object(self.transaction_repository, "create", reject_after_writes):
            reject("transfer", {
                "source_account_number": SOURCE, "destination_account_number": DESTINATION, "amount": "10",
            }, 422, "TEST_REJECTION")
        self.assertEqual(self.client.portal.call(state), baseline)

        own = self.check(self.request("GET", "/transactions", actor="client_a"), 200, "TRANSACTIONS_FETCHED")
        records = [item for item in own["items"] if item["idempotency_key"] in attempts]
        self.assertEqual(len(records), len(attempts))
        for item in records:
            correlation, code, amount = attempts[item["idempotency_key"]]
            self.assertEqual(item["status"], "ERROR")
            self.assertEqual(item["failure_code"], code)
            self.assertEqual(item["correlation_id"], correlation)
            self.assertEqual(Decimal(item["original_amount"]), amount)
            self.assertEqual(Decimal(item["fee_charged"]), 0)
            self.assertEqual(Decimal(item["converted_amount"]), 0)
            if code == "RESOURCE_NOT_FOUND":
                self.assertEqual(item["destination_currency"], "XXX")
        for outsider in ("client_b", "admin_b"):
            visible = self.check(self.request("GET", "/transactions", actor=outsider), 200, "TRANSACTIONS_FETCHED")
            self.assertTrue(all(item["idempotency_key"] not in attempts for item in visible["items"]))
        admin = self.check(self.request("GET", "/transactions", actor="admin_a"), 200, "TRANSACTIONS_FETCHED")
        self.assertEqual(sum(item["idempotency_key"] in attempts for item in admin["items"]), len(attempts))
