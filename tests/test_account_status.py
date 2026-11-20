"""Cambios de estado, auditoría y exclusión concurrente en PostgreSQL."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
from threading import Event
import unittest
from unittest.mock import patch
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text

from demo_seed_fixture import load_demo_seed


API = "/api/v1"


@unittest.skipUnless(
    os.getenv("BANKOS_TEST_DATABASE_URL") and os.getenv("BANKOS_TEST_REDIS_URL"),
    "Requiere PostgreSQL y Redis desechables",
)
class AccountStatusTest(unittest.TestCase):
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

        from app.core.database import async_session_factory
        from app.main import app
        from scripts.seed_demo import USERS

        cls.sessions = async_session_factory
        cls.client = stack.enter_context(TestClient(app))
        cls.client.portal.call(load_demo_seed)
        for label, slug, index in (
            ("owner", "daviplata", 0),
            ("outsider", "daviplata", 1),
            ("foreign", "falabella", 0),
            ("admin", "banco-andino", 0),
            ("client_andino", "banco-andino", 2),
        ):
            user = USERS[slug][index]
            response = cls.client.post(f"{API}/auth/login", json={
                "tenant_slug": slug, "email": user["email"], "password": user["password"],
            })
            if response.status_code != 200:
                raise AssertionError(response.text)
            setattr(cls, label, response.json()["data"])

    def headers(self, actor):
        return {"Authorization": f"Bearer {actor['access_token']}"}

    def check(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.json()["internal_code"], code, response.text)
        return response.json()["data"]

    def open_account(self, actor=None, **fields):
        data = self.check(self.client.post(f"{API}/accounts", headers=self.headers(actor or self.owner), json={
            "currency": "COP", "initial_deposit": "100.50", **fields,
        }), 201, "ACCOUNT_CREATED")
        return data["account_number"]

    def transition(self, number, operation, actor=None):
        return self.client.patch(f"{API}/accounts/{number}/{operation}", headers=self.headers(actor or self.owner))

    def detail(self, number):
        return self.check(self.client.get(
            f"{API}/accounts/{number}", headers=self.headers(self.owner),
        ), 200, "ACCOUNT_DETAIL_FETCHED")

    async def audit(self, number):
        async with self.sessions() as session:
            rows = await session.execute(text("""
                SELECT action, user_id, tenant_id, previous_state, new_state FROM audit_logs
                WHERE new_state->>'account_number' = :number
                  AND action IN ('ACCOUNT_DEACTIVATED', 'ACCOUNT_REACTIVATED')
                ORDER BY created_at
            """), {"number": number})
            return rows.all()

    def test_owner_reactivation_cooldown_and_balance_history(self):
        number = self.open_account()
        owner_headers = self.headers(self.owner)

        def operation(path, body):
            return self.client.post(f"{API}/transactions/{path}", headers={
                **owner_headers, "Idempotency-Key": str(uuid.uuid4()),
            }, json=body)

        deposit = self.check(operation("deposit", {"account_number": number, "amount": "5"}), 201, "DEPOSIT_SUCCESS")
        initial = self.detail(number)
        self.assertIsNone(initial["reactivated_at"])
        disabled = self.check(self.transition(number, "deactivate"), 200, "ACCOUNT_DEACTIVATED")
        self.assertEqual(disabled["status"], "INACTIVE")
        repeated = self.check(self.transition(number, "deactivate"), 200, "ACCOUNT_DEACTIVATED")
        self.assertEqual(repeated, disabled)

        for kind, body in (
            ("deposit", {"account_number": number, "amount": "5"}),
            ("withdraw", {"account_number": number, "amount": "5"}),
            ("transfer", {"source_account_number": number, "destination_account_number": "3300000003", "amount": "5"}),
        ):
            with self.subTest(operation=kind):
                self.check(operation(kind, body), 400, "ACCOUNT_INACTIVE")
        self.assertEqual(Decimal(self.detail(number)["balance"]), Decimal("105.50"))

        activated = self.check(self.transition(number, "reactivate"), 200, "ACCOUNT_REACTIVATED")
        activated_at = datetime.fromisoformat(activated["reactivated_at"])
        next_allowed = datetime.fromisoformat(activated["can_deactivate_at"])
        self.assertEqual(next_allowed - activated_at, timedelta(hours=24))
        self.assertEqual(self.detail(number), activated)
        self.assertEqual(self.check(self.transition(number, "reactivate"), 200, "ACCOUNT_REACTIVATED"), activated)
        locked = self.check(self.transition(number, "deactivate"), 409, "ACCOUNT_DEACTIVATION_COOLDOWN")
        self.assertEqual(datetime.fromisoformat(locked["can_deactivate_at"]), next_allowed)

        async def expire():
            async with self.sessions.begin() as session:
                await session.execute(text("""
                    UPDATE accounts SET reactivated_at = :past, can_deactivate_at = :eligible
                    WHERE account_number = :number AND tenant_id = :tenant
                """), {"past": activated_at - timedelta(days=2), "eligible": next_allowed - timedelta(days=2),
                        "number": number, "tenant": uuid.UUID(self.owner["tenant_id"])})

        self.client.portal.call(expire)
        again = self.check(self.transition(number, "deactivate"), 200, "ACCOUNT_DEACTIVATED")
        self.assertEqual(again["status"], "INACTIVE")
        self.assertEqual(again["balance"], activated["balance"])
        history = self.check(self.client.get(
            f"{API}/transactions", headers=owner_headers, params={"account_number": number},
        ), 200, "TRANSACTIONS_FETCHED")["items"]
        self.assertEqual([item["id"] for item in history if item["status"] == "SUCCESS"], [deposit["id"]])

        logs = self.client.portal.call(self.audit, number)
        self.assertEqual([row.action for row in logs], ["ACCOUNT_DEACTIVATED", "ACCOUNT_REACTIVATED", "ACCOUNT_DEACTIVATED"])
        self.assertEqual([(row.previous_state["status"], row.new_state["status"]) for row in logs], [
            ("ACTIVE", "INACTIVE"), ("INACTIVE", "ACTIVE"), ("ACTIVE", "INACTIVE"),
        ])
        self.assertTrue(all(str(row.user_id) == self.owner["user_id"] for row in logs))
        self.assertTrue(all(row.previous_state["balance"] == row.new_state["balance"] for row in logs))

    def test_foreign_and_blocked_accounts(self):
        number = self.open_account()
        for operation in ("deactivate", "reactivate"):
            self.check(self.transition(number, operation, self.outsider), 403, "AUTHORIZATION_FAILED")
            self.check(self.transition(number, operation, self.foreign), 404, "RESOURCE_NOT_FOUND")

        async def block():
            async with self.sessions.begin() as session:
                await session.execute(text("""
                    UPDATE accounts SET status = 'BLOCKED'
                    WHERE account_number = :number AND tenant_id = :tenant
                """), {"number": number, "tenant": uuid.UUID(self.owner["tenant_id"])})

        self.client.portal.call(block)
        for operation in ("deactivate", "reactivate"):
            self.check(self.transition(number, operation), 403, "ACCOUNT_BLOCKED")
        self.assertEqual(self.detail(number)["status"], "BLOCKED")
        self.assertEqual(self.client.portal.call(self.audit, number), [])

    def test_admin_flow_and_configured_cooldown(self):
        from app.core.config import settings

        number = self.open_account(self.admin, owner_id=self.client_andino["user_id"])
        disabled = self.check(self.transition(number, "deactivate", self.admin), 200, "ACCOUNT_DEACTIVATED")
        self.assertTrue(disabled["requires_admin_reactivation"])
        self.check(self.transition(number, "reactivate", self.client_andino), 403, "ACCOUNT_REACTIVATION_REQUIRES_ADMIN")
        with patch.object(settings, "ACCOUNT_DEACTIVATION_COOLDOWN_HOURS", 2):
            enabled = self.check(self.transition(number, "reactivate", self.admin), 200, "ACCOUNT_REACTIVATED")
        self.assertFalse(enabled["requires_admin_reactivation"])
        self.assertEqual(datetime.fromisoformat(enabled["can_deactivate_at"]) -
                         datetime.fromisoformat(enabled["reactivated_at"]), timedelta(hours=2))
        self.check(self.transition(number, "deactivate", self.client_andino), 409, "ACCOUNT_DEACTIVATION_COOLDOWN")
        self.check(self.transition(number, "deactivate", self.admin), 200, "ACCOUNT_DEACTIVATED")

    def test_concurrent_requests_use_the_state_after_the_row_lock(self):
        from app.modules.accounts.infrastructure.repositories import PostgresAccountRepository

        number = self.open_account()
        self.check(self.transition(number, "deactivate"), 200, "ACCOUNT_DEACTIVATED")
        first_locked, other_started, release = Event(), Event(), Event()
        get_for_update = PostgresAccountRepository.get_for_update

        async def hold_first(repository, account_number, tenant_id):
            if first_locked.is_set():
                other_started.set()
                return await get_for_update(repository, account_number, tenant_id)
            row = await get_for_update(repository, account_number, tenant_id)
            first_locked.set()
            if not await asyncio.to_thread(release.wait, 8):
                raise AssertionError("No se liberó el cambio de estado de prueba")
            return row

        with patch.object(PostgresAccountRepository, "get_for_update", hold_first), ThreadPoolExecutor(2) as executor:
            first = executor.submit(self.transition, number, "reactivate")
            try:
                self.assertTrue(first_locked.wait(5))
                second = executor.submit(self.transition, number, "deactivate")
                self.assertTrue(other_started.wait(5))
                self.assertFalse(second.done())
            finally:
                release.set()
            active = self.check(first.result(timeout=5), 200, "ACCOUNT_REACTIVATED")
            rejected = self.check(second.result(timeout=5), 409, "ACCOUNT_DEACTIVATION_COOLDOWN")
        self.assertEqual(datetime.fromisoformat(active["can_deactivate_at"]), datetime.fromisoformat(rejected["can_deactivate_at"]))
        self.assertEqual(self.detail(number)["status"], "ACTIVE")
        self.assertEqual([row.action for row in self.client.portal.call(self.audit, number)], [
            "ACCOUNT_DEACTIVATED", "ACCOUNT_REACTIVATED",
        ])
