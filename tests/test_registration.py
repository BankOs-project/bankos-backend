"""Pruebas de alta y vinculación con PostgreSQL, Redis y correo simulado."""

from contextlib import ExitStack, redirect_stdout
from io import StringIO
import os
import unittest
from unittest.mock import AsyncMock, patch
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text


API = "/api/v1"
START = f"{API}/auth/register"
VERIFY = f"{START}/verify"
PASSWORD_A = "Prueba-Banco-A-93!"
PASSWORD_B = "Prueba-Banco-B-61!"


@unittest.skipUnless(
    os.getenv("BANKOS_TEST_DATABASE_URL") and os.getenv("BANKOS_TEST_REDIS_URL"),
    "Requiere PostgreSQL y Redis desechables",
)
class RegistrationTest(unittest.TestCase):
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
        from app.core.security import decode_access_token
        from app.main import app

        cls.session_factory = async_session_factory
        cls.decode_token = staticmethod(decode_access_token)
        cls.mail = stack.enter_context(patch(
            "app.modules.auth_users.application.registration.send_registration_code", new_callable=AsyncMock,
        ))

        async def with_client_ip(scope, receive, send):
            peer = dict(scope.get("headers", [])).get(b"x-test-peer")
            if peer:
                scope = {**scope, "client": (peer.decode(), 1234)}
            await app(scope, receive, send)

        with redirect_stdout(StringIO()):
            cls.client = stack.enter_context(TestClient(with_client_ip))
        banks = {entry["name"]: entry["bank_id"] for entry in cls.client.get(f"{API}/banks").json()["data"]}
        cls.bank_a = banks["Banco Andino S.A."]
        cls.bank_b = banks["Cooperativa Caribe Ltda."]

    def setUp(self):
        self.mail.reset_mock()
        peers = {
            "test_link_requires_both_mailboxes_and_keeps_tenant_scope": "203.0.113.81",
            "test_existing_user_can_link_without_changing_accounts": "203.0.113.82",
            "test_registration_expiry_and_same_bank_email_uniqueness": "203.0.113.83",
        }
        self.headers = {"X-Test-Peer": peers[self._testMethodName]}

    def check(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.json()["internal_code"], code)
        self.assertNotIn("person_id", response.text)
        return response.json()["data"]

    def request(self, path, body, token=None):
        headers = {**self.headers, **({"Authorization": f"Bearer {token}"} if token else {})}
        return self.client.post(path, headers=headers, json=body)

    def begin(self, bank, email, token=None, password=None):
        body = {"bank_id": bank, "email": email}
        if password is not None:
            body["source_password"] = password
        result = self.check(self.request(START, body, token), 202, "REGISTRATION_PENDING")
        self.assertEqual(set(result), {"registration_id", "expires_in"})
        self.assertEqual(result["expires_in"], 1200)
        return result["registration_id"]

    def login(self, bank, email, password):
        return self.request(f"{API}/auth/login", {"bank_id": bank, "email": email, "password": password})

    def verify_body(self, registration_id, password=PASSWORD_A):
        return {
            "registration_id": registration_id,
            "email_code": self.mail.await_args.args[1],
            "full_name": "Cliente de prueba",
            "password": password,
            "role": "administrador",
        }

    async def user_rows(self, *users):
        async with self.session_factory() as session:
            result = await session.execute(text("""
                SELECT id, tenant_id, person_id, email_verified_at, email, role
                FROM users WHERE id = ANY(CAST(:ids AS uuid[]))
            """), {"ids": [uuid.UUID(user) for user in users]})
            return {str(row.id): row for row in result}

    def test_link_requires_both_mailboxes_and_keeps_tenant_scope(self):
        email_a = f"persona-{uuid.uuid4().hex}@example.com"
        email_b = f"otro-{uuid.uuid4().hex}@example.com"
        pending = self.begin(self.bank_a, email_a)
        first = self.verify_body(pending)
        self.check(self.login(self.bank_a, email_a, PASSWORD_A), 401, "AUTHENTICATION_FAILED")
        self.check(self.request(VERIFY, {**first, "email_code": "incorrecto"}), 400, "REGISTRATION_INVALID")
        self.check(self.request(VERIFY, first), 201, "REGISTRATION_VERIFIED")
        self.check(self.request(VERIFY, first), 400, "REGISTRATION_INVALID")
        a = self.check(self.login(self.bank_a, email_a, PASSWORD_A), 200, "LOGIN_SUCCESS")
        header_a = {"Authorization": f"Bearer {a['access_token']}"}
        own_account = self.check(self.client.post(
            f"{API}/accounts", headers=header_a, json={"currency": "COP"},
        ), 201, "ACCOUNT_CREATED")

        self.check(self.request(START, {
            "bank_id": self.bank_b, "email": email_b, "source_password": "incorrecta",
        }, a["access_token"]), 401, "AUTHENTICATION_FAILED")
        linked = self.begin(self.bank_b, email_b, token=a["access_token"], password=PASSWORD_A)
        target_mail, source_mail = self.mail.await_args_list[-2:]
        self.assertEqual(target_mail.args[0], email_b)
        self.assertEqual(source_mail.args[0], email_a)
        self.assertTrue(source_mail.kwargs["source"])
        self.assertNotEqual(target_mail.args[1], source_mail.args[1])
        second = {**self.verify_body(linked, PASSWORD_B), "email_code": target_mail.args[1]}
        self.check(self.request(VERIFY, second, a["access_token"]), 400, "REGISTRATION_INVALID")
        second["source_email_code"] = source_mail.args[1]
        self.check(self.request(VERIFY, second), 400, "REGISTRATION_INVALID")
        self.check(self.request(VERIFY, second, a["access_token"]), 201, "REGISTRATION_VERIFIED")

        b = self.check(self.login(self.bank_b, email_b, PASSWORD_B), 200, "LOGIN_SUCCESS")
        self.check(self.login(self.bank_b, email_b, PASSWORD_A), 401, "AUTHENTICATION_FAILED")
        self.check(self.login(self.bank_b, email_a, PASSWORD_A), 401, "AUTHENTICATION_FAILED")
        self.assertNotEqual(a["user_id"], b["user_id"])
        self.assertNotEqual(a["tenant_id"], b["tenant_id"])
        self.assertEqual((a["role"], b["role"]), ("cliente", "cliente"))
        for record in (a, b):
            claims = self.decode_token(record["access_token"])
            self.assertEqual(claims.tenant_id, record["tenant_id"])
            self.assertEqual(claims.user_id, record["user_id"])
            self.assertNotIn("person_id", claims.model_dump())

        rows = self.client.portal.call(self.user_rows, a["user_id"], b["user_id"])
        self.assertEqual(rows[a["user_id"]].person_id, rows[b["user_id"]].person_id)
        for user in rows.values():
            self.assertIsNotNone(user.email_verified_at)
            self.assertNotEqual(user.id, user.person_id)
        header_b = {"Authorization": f"Bearer {b['access_token']}"}
        profile = self.check(self.client.get(f"{API}/auth/me", headers=header_b), 200, "USER_PROFILE")
        self.assertEqual(profile["email"], email_b)
        self.assertNotIn(email_a, str(profile))
        self.assertEqual(self.check(self.client.get(
            f"{API}/accounts", headers=header_b,
        ), 200, "ACCOUNTS_FETCHED")["items"], [])
        self.check(self.client.get(
            f"{API}/accounts/{own_account['account_number']}", headers=header_b,
        ), 404, "RESOURCE_NOT_FOUND")

    def test_existing_user_can_link_without_changing_accounts(self):
        from scripts.seed_demo import USERS

        user = USERS["banco-andino"][0]
        source = self.check(self.login(self.bank_a, user["email"], user["password"]), 200, "LOGIN_SUCCESS")
        before = self.client.portal.call(self.user_rows, source["user_id"])[source["user_id"]]
        self.assertIsNone(before.email_verified_at)
        self.assertEqual(before.id, user["id"])
        new_email = f"legado-{uuid.uuid4().hex}@example.com"
        flow = self.begin(self.bank_b, new_email, source["access_token"], user["password"])
        target_mail, source_mail = self.mail.await_args_list[-2:]
        self.check(self.request(VERIFY, {
            **self.verify_body(flow), "email_code": target_mail.args[1], "source_email_code": source_mail.args[1],
        }, source["access_token"]), 201, "REGISTRATION_VERIFIED")
        target = self.check(self.login(self.bank_b, new_email, PASSWORD_A), 200, "LOGIN_SUCCESS")
        after = self.client.portal.call(self.user_rows, source["user_id"], target["user_id"])
        self.assertEqual(after[source["user_id"]].person_id, before.person_id)
        self.assertEqual(after[source["user_id"]].role, "administrador")
        self.assertIsNotNone(after[source["user_id"]].email_verified_at)
        self.assertEqual(after[target["user_id"]].person_id, before.person_id)
        self.assertEqual(after[target["user_id"]].role, "cliente")

    def test_registration_expiry_and_same_bank_email_uniqueness(self):
        email = f"repetido-{uuid.uuid4().hex}@example.com"
        flow = self.begin(self.bank_a, email)
        self.check(self.request(VERIFY, self.verify_body(flow)), 201, "REGISTRATION_VERIFIED")
        duplicate = self.begin(self.bank_a, email)
        self.check(self.request(VERIFY, self.verify_body(duplicate, PASSWORD_B)), 400, "REGISTRATION_INVALID")
        self.check(self.login(self.bank_a, email, PASSWORD_A), 200, "LOGIN_SUCCESS")
        self.check(self.login(self.bank_a, email, PASSWORD_B), 401, "AUTHENTICATION_FAILED")

        other_bank = self.begin(self.bank_b, email)
        self.check(self.request(VERIFY, self.verify_body(other_bank, PASSWORD_B)), 201, "REGISTRATION_VERIFIED")
        a = self.check(self.login(self.bank_a, email, PASSWORD_A), 200, "LOGIN_SUCCESS")
        b = self.check(self.login(self.bank_b, email, PASSWORD_B), 200, "LOGIN_SUCCESS")
        people = self.client.portal.call(self.user_rows, a["user_id"], b["user_id"])
        self.assertNotEqual(people[a["user_id"]].person_id, people[b["user_id"]].person_id)

        expired = self.begin(self.bank_b, f"expirado-{uuid.uuid4().hex}@example.com")

        async def expire():
            async with self.session_factory.begin() as session:
                await session.execute(text("""
                    UPDATE registration_challenges SET expires_at = now() - interval '1 second'
                    WHERE id = :id
                """), {"id": uuid.UUID(expired)})

        self.client.portal.call(expire)
        self.check(self.request(VERIFY, self.verify_body(expired)), 400, "REGISTRATION_INVALID")
