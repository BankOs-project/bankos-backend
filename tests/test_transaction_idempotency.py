"""Pruebas HTTP con PostgreSQL y Redis de prueba, ambos aislados."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from decimal import Decimal
import json
import os
from threading import Event
import unittest
from unittest.mock import patch
import uuid

from fastapi.testclient import TestClient
from demo_seed_fixture import load_demo_seed
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


API = "/api/v1"
SOURCE = "1000000002"
DESTINATION = "1000000003"
OTHER_TENANT_ACCOUNT = "2000000001"
OPERATIONS = {
    "deposit": ({"account_number": SOURCE, "amount": "10"}, Decimal("10"), Decimal("0")),
    "withdraw": ({"account_number": SOURCE, "amount": "10"}, Decimal("-10"), Decimal("0")),
    "transfer": (
        {"source_account_number": SOURCE, "destination_account_number": DESTINATION, "amount": "10"},
        Decimal("-10.15"),
        Decimal("42000"),
    ),
}


class DropResponse:
    """Permite que el servidor termine el envío sin entregarlo al cliente HTTP."""

    def __init__(self, app):
        self.app = app
        self.messages = []

    async def __call__(self, scope, receive, send):
        headers = dict(scope.get("headers", []))
        if peer := headers.get(b"x-test-peer"):
            scope = {**scope, "client": (peer.decode(), 1234)}
        if headers.get(b"x-test-drop-response") == b"1":
            self.messages.clear()

            async def discard(message):
                self.messages.append(message)

            await self.app(scope, receive, discard)
        else:
            await self.app(scope, receive, send)


@unittest.skipUnless(
    os.getenv("BANKOS_TEST_DATABASE_URL") and os.getenv("BANKOS_TEST_REDIS_URL"),
    "Define BANKOS_TEST_DATABASE_URL y BANKOS_TEST_REDIS_URL para servicios desechables",
)
class TransactionIdempotencyTest(unittest.TestCase):
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
        from app.core.middlewares.idempotency import IdempotencyContext
        from app.main import app
        from scripts.seed_demo import USERS

        cls.session_factory = async_session_factory
        cls.idempotency_context = IdempotencyContext
        cls.transport = DropResponse(app)
        stack.enter_context(patch(
            "app.modules.transactions.presentation.router.HttpxWebhookDispatcher.dispatch",
            autospec=True,
        ))
        cls.client = stack.enter_context(TestClient(cls.transport, raise_server_exceptions=False))
        cls.client.portal.call(load_demo_seed)

        cls.sessions = []
        for tenant, index in (("banco-andino", 1), ("banco-andino", 2), ("cooperativa-caribe", 1)):
            user = USERS[tenant][index]
            response = cls.client.post(f"{API}/auth/login", json={
                "tenant_slug": tenant, "email": user["email"], "password": user["password"],
            })
            if response.status_code != 200:
                raise AssertionError(response.text)
            cls.sessions.append(response.json()["data"])

    def cache_key(self, key, actor=0):
        identity = self.sessions[actor]
        return f"idemp:receipt:v1:{identity['tenant_id']}:{identity['user_id']}:{key}"

    async def inspect_cache(self, key, delete=False):
        async with Redis.from_url(os.environ["BANKOS_TEST_REDIS_URL"], decode_responses=True) as redis:
            if delete:
                await redis.delete(key)
            raw = await redis.get(key)
            return (json.loads(raw) if raw else None), await redis.pttl(key)

    async def read_state(self):
        async with self.session_factory() as session:
            accounts = await session.execute(text("""
                SELECT account_number, balance FROM accounts
                WHERE account_number IN (:source, :destination, :other)
            """), {"source": SOURCE, "destination": DESTINATION, "other": OTHER_TENANT_ACCOUNT})
            transactions = await session.scalar(text("SELECT count(*) FROM transactions"))
            receipts = await session.scalar(text("SELECT count(*) FROM transaction_idempotency_receipts"))
            balances = {number: balance for number, balance in accounts}
            return balances, transactions, receipts

    def assert_change(self, baseline, operation, count):
        before, transactions_before, receipts_before = baseline
        after, transactions_after, receipts_after = self.client.portal.call(self.read_state)
        _, source_delta, destination_delta = OPERATIONS[operation]
        self.assertEqual(after[SOURCE] - before[SOURCE], source_delta * count)
        self.assertEqual(after[DESTINATION] - before[DESTINATION], destination_delta * count)
        self.assertEqual(after[OTHER_TENANT_ACCOUNT], before[OTHER_TENANT_ACCOUNT])
        self.assertEqual(transactions_after - transactions_before, count)
        self.assertEqual(receipts_after - receipts_before, count)

    def send(self, operation, key, correlation, *, actor=0, body=None, drop=False):
        headers = {
            "Authorization": f"Bearer {self.sessions[actor]['access_token']}",
            "Idempotency-Key": key,
            "X-Correlation-ID": correlation,
            "X-Test-Peer": f"203.0.113.{uuid.UUID(key).int % 250 + 1}",
        }
        if drop:
            headers["X-Test-Drop-Response"] = "1"
        response = self.client.post(f"{API}/transactions/{operation}", json=body or OPERATIONS[operation][0], headers=headers)
        if not drop:
            self.assertEqual(response.headers["X-Correlation-ID"], correlation)
        return response

    def assert_success(self, response, key, correlation):
        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["idempotency_key"], key)
        self.assertEqual(payload["data"]["correlation_id"], correlation)

    def test_commit_then_cache_and_exact_replay(self):
        for operation in OPERATIONS:
            with self.subTest(operation=operation):
                baseline = self.client.portal.call(self.read_state)
                key, correlation = str(uuid.uuid4()), str(uuid.uuid4())
                original = self.send(operation, key, correlation)
                self.assert_success(original, key, correlation)
                cached, ttl = self.client.portal.call(self.inspect_cache, self.cache_key(key))
                self.assertGreater(ttl, 86_350_000)
                self.assertLessEqual(ttl, 86_400_000)
                self.assertEqual(cached["status_code"], original.status_code)
                self.assertEqual(cached["body"].encode(), original.content)
                replay = self.send(operation, key, str(uuid.uuid4()))
                self.assertEqual((replay.status_code, replay.content), (201, original.content))
                self.assert_change(baseline, operation, 1)

    def test_concurrent_requests(self):
        confirm = self.idempotency_context.confirm
        for operation in OPERATIONS:
            with self.subTest(operation=operation):
                baseline = self.client.portal.call(self.read_state)
                key, correlation = str(uuid.uuid4()), str(uuid.uuid4())
                ready, proceed = Event(), Event()

                async def pause_before_commit(context, *args, **kwargs):
                    ready.set()
                    if not await asyncio.to_thread(proceed.wait, 8):
                        raise RuntimeError("La solicitud concurrente no terminó")
                    return await confirm(context, *args, **kwargs)

                with patch.object(self.idempotency_context, "confirm", pause_before_commit):
                    with ThreadPoolExecutor() as threads:
                        first = threads.submit(self.send, operation, key, correlation)
                        try:
                            self.assertTrue(ready.wait(5), "No se llegó al commit")
                            competing = self.send(operation, key, correlation)
                            self.assertEqual(competing.status_code, 409, competing.text)
                            self.assertEqual(competing.json()["internal_code"], "IDEMPOTENCY_IN_PROGRESS")
                            self.assert_change(baseline, operation, 0)
                        finally:
                            proceed.set()
                        self.assert_success(first.result(timeout=5), key, correlation)
                self.assert_change(baseline, operation, 1)

    def test_failure_before_commit_rolls_back_and_releases_key(self):
        async def abort_commit(session):
            # PostgreSQL aborta una transacción que ya tiene los cambios y el recibo.
            await session.execute(text("SELECT 1 / 0"))

        for operation in OPERATIONS:
            with self.subTest(operation=operation):
                baseline = self.client.portal.call(self.read_state)
                key, correlation = str(uuid.uuid4()), str(uuid.uuid4())
                with patch.object(AsyncSession, "commit", abort_commit):
                    with self.assertLogs("bankos.exceptions", level="ERROR") as logs:
                        failed = self.send(operation, key, correlation)
                self.assertEqual(failed.status_code, 500, failed.text)
                self.assertEqual(logs.records[-1].correlation_id, correlation)
                self.assertEqual(
                    self.client.portal.call(self.inspect_cache, self.cache_key(key)), (None, -2),
                )
                self.assert_change(baseline, operation, 0)
                self.assert_success(self.send(operation, key, correlation), key, correlation)
                self.assert_change(baseline, operation, 1)

    def test_lost_response_after_commit_with_no_redis_copy(self):
        set_cache = Redis.set

        async def unavailable(redis, name, *args, **kwargs):
            if name.startswith("idemp:receipt:v1:"):
                raise RedisConnectionError("Fallo de Redis al publicar el comprobante")
            return await set_cache(redis, name, *args, **kwargs)

        for operation in OPERATIONS:
            with self.subTest(operation=operation):
                baseline = self.client.portal.call(self.read_state)
                key, correlation = str(uuid.uuid4()), str(uuid.uuid4())
                with patch.object(Redis, "set", unavailable):
                    with self.assertLogs("bankos.idempotency", level="WARNING"):
                        discarded = self.send(operation, key, correlation, drop=True)
                self.assertEqual(discarded.content, b"")
                start = next(m for m in self.transport.messages if m["type"] == "http.response.start")
                original = b"".join(
                    m.get("body", b"") for m in self.transport.messages if m["type"] == "http.response.body"
                )
                self.assertEqual(start["status"], 201)
                self.assertEqual(
                    self.client.portal.call(self.inspect_cache, self.cache_key(key)), (None, -2),
                )
                self.assert_change(baseline, operation, 1)
                replay = self.send(operation, key, correlation)
                self.assert_success(replay, key, correlation)
                self.assertEqual(replay.content, original)
                cached, ttl = self.client.portal.call(self.inspect_cache, self.cache_key(key))
                self.assertGreater(ttl, 0)
                self.assertEqual(cached["body"].encode(), original)
                self.assert_change(baseline, operation, 1)

    def test_commit_confirmation_lost_before_redis(self):
        baseline = self.client.portal.call(self.read_state)
        key, correlation = str(uuid.uuid4()), str(uuid.uuid4())
        commit = AsyncSession.commit

        async def lose_confirmation(session):
            await commit(session)
            raise ConnectionError("Se perdió la confirmación del commit")

        with patch.object(AsyncSession, "commit", lose_confirmation):
            with self.assertLogs("bankos.exceptions", level="ERROR"):
                uncertain = self.send("transfer", key, correlation)
        self.assertEqual(uncertain.status_code, 500)
        self.assertEqual(self.client.portal.call(self.inspect_cache, self.cache_key(key)), (None, -2))
        self.assert_change(baseline, "transfer", 1)

        replay = self.send("transfer", key, correlation)
        self.assert_success(replay, key, correlation)
        self.assert_change(baseline, "transfer", 1)

    def test_scope_and_expiration(self):
        key, correlation = str(uuid.uuid4()), str(uuid.uuid4())
        operations = [
            self.send("deposit", key, correlation, actor=actor, body={"account_number": number, "amount": "1"})
            for actor, number in enumerate((SOURCE, DESTINATION, OTHER_TENANT_ACCOUNT))
        ]
        for result in operations:
            self.assert_success(result, key, correlation)
        self.assertEqual(len({result.json()["data"]["id"] for result in operations}), 3)

        async def expire_receipt():
            async with self.session_factory.begin() as session:
                await session.execute(text("""
                    UPDATE transaction_idempotency_receipts
                    SET expires_at = clock_timestamp() - interval '1 second'
                    WHERE key = :key AND user_id = :user_id
                """), {"key": uuid.UUID(key), "user_id": uuid.UUID(self.sessions[0]["user_id"])})
            await self.inspect_cache(self.cache_key(key), delete=True)

        self.client.portal.call(expire_receipt)
        next_operation = self.send("deposit", key, correlation)
        self.assert_success(next_operation, key, correlation)
        self.assertNotEqual(next_operation.json()["data"]["id"], operations[0].json()["data"]["id"])
        other_user_replay = self.send(
            "deposit", key, correlation, actor=1, body={"account_number": DESTINATION, "amount": "1"},
        )
        self.assertEqual(other_user_replay.content, operations[1].content)
