"""Contrato del directorio público y aislamiento del login con PostgreSQL y Redis."""

from contextlib import ExitStack
import os
import unittest
from unittest.mock import patch
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text
from demo_seed_fixture import load_demo_seed


API = "/api/v1"
BANK_A = "banco-andino"
BANK_B = "cooperativa-caribe"
AUTHENTICATION_FAILED = {
    "success": False,
    "internal_code": "AUTHENTICATION_FAILED",
    "message": "Institución bancaria o credenciales no válidas",
    "data": None,
}


@unittest.skipUnless(
    os.getenv("BANKOS_TEST_DATABASE_URL") and os.getenv("BANKOS_TEST_REDIS_URL"),
    "Requiere PostgreSQL y Redis desechables",
)
class BankDirectoryTest(unittest.TestCase):
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
        from app.modules.tenants.infrastructure.models import TenantModel
        from scripts.seed_demo import TENANTS, USERS

        cls.session_factory = async_session_factory
        cls.tenant_model = TenantModel
        cls.internal_ids = {row["slug"]: row["id"] for row in TENANTS}
        cls.users = USERS
        cls.decode_token = staticmethod(decode_access_token)

        async def with_client_ip(scope, receive, send):
            peer = dict(scope.get("headers", [])).get(b"x-test-peer")
            if peer:
                scope = {**scope, "client": (peer.decode(), 1234)}
            await app(scope, receive, send)

        cls.client = stack.enter_context(TestClient(with_client_ip))
        cls.client.portal.call(load_demo_seed)

    def directory(self):
        response = self.client.get(f"{API}/banks")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["internal_code"], "BANKS_FETCHED")
        return {bank["name"]: bank for bank in response.json()["data"]}

    async def configure_demo_bank(self, slug, enabled):
        async with self.session_factory.begin() as session:
            return await session.scalar(text("""
                UPDATE tenants SET directory_enabled = :enabled
                WHERE slug = :slug RETURNING public_id
            """), {"enabled": enabled, "slug": slug})

    def login(self, selector, credentials, peer):
        return self.client.post(f"{API}/auth/login", json={
            **selector, "email": credentials["email"], "password": credentials["password"],
        }, headers={"X-Test-Peer": peer})

    def assert_authentication_failed(self, response):
        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(response.json(), AUTHENTICATION_FAILED)

    def test_directory_limits_fields_and_keeps_id_after_rename(self):
        logo = "https://example.com/logo.png"

        async def create_banks():
            async with self.session_factory.begin() as session:
                for slug, name, settings in (
                    ("directory-hidden", "Directorio oculto", {}),
                    ("directory-inactive", "Directorio inactivo", {"directory_enabled": True, "is_active": False}),
                    ("directory-logo", "Directorio con logo", {"directory_enabled": True, "logo_url": logo}),
                ):
                    session.add(self.tenant_model(
                        name=name, slug=slug, max_transaction_limit=100,
                        fee_type="FIXED", fee_value=0, webhook_url="https://example.com/internal",
                        **settings,
                    ))

        self.client.portal.call(create_banks)
        entries = self.directory()
        self.assertNotIn("Directorio oculto", entries)
        self.assertNotIn("Directorio inactivo", entries)
        self.assertEqual(entries["Directorio con logo"]["logo_url"], logo)
        self.assertIsNone(entries["Banco Andino S.A."]["logo_url"])
        self.assertEqual(len({bank["bank_id"] for bank in entries.values()}), len(entries))
        for bank in entries.values():
            self.assertEqual(set(bank), {"bank_id", "name", "logo_url"})
            public_id = uuid.UUID(bank["bank_id"])
            self.assertEqual(public_id.version, 4)
            self.assertNotIn(public_id, self.internal_ids.values())

        async def rename_bank():
            async with self.session_factory.begin() as session:
                await session.execute(text("""
                    UPDATE tenants SET name = 'Directorio renombrado', slug = 'directory-renamed'
                    WHERE slug = 'directory-logo'
                """))

        self.client.portal.call(rename_bank)
        self.assertEqual(self.directory()["Directorio renombrado"]["bank_id"], entries["Directorio con logo"]["bank_id"])

    def test_login_and_resource_access_remain_scoped_to_the_bank(self):
        banks = self.directory()
        bank_a = banks["Banco Andino S.A."]["bank_id"]
        bank_b = banks["Cooperativa Caribe Ltda."]["bank_id"]
        credentials = self.users[BANK_A][1]
        peer = "203.0.113.60"
        response = self.login({"bank_id": bank_a}, credentials, peer)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        claims = self.decode_token(data["access_token"])
        self.assertEqual(claims.tenant_id, str(self.internal_ids[BANK_A]))
        self.assertEqual(claims.user_id, str(credentials["id"]))
        self.assertEqual(claims.role, "cliente")
        self.assertNotIn("bank_id", claims.model_dump())

        for selector, login_credentials in (
            ({"bank_id": bank_b}, credentials),
            ({"bank_id": str(uuid.uuid4())}, credentials),
            ({"bank_id": "id-invalido"}, credentials),
            ({"bank_id": bank_a}, {**self.users[BANK_A][2], "password": "incorrecta"}),
            ({"bank_id": bank_b, "tenant_slug": BANK_A}, credentials),
        ):
            self.assert_authentication_failed(self.login(selector, login_credentials, peer))

        legacy = self.login({"tenant_slug": BANK_A}, credentials, peer)
        self.assertEqual(legacy.status_code, 200, legacy.text)
        self.assertEqual(legacy.json()["data"]["tenant_id"], data["tenant_id"])

        headers = {"Authorization": f"Bearer {data['access_token']}", "X-Bank-ID": bank_b}
        accounts = self.client.get(f"{API}/accounts", headers=headers, params={"bank_id": bank_b})
        self.assertEqual(accounts.status_code, 200, accounts.text)
        items = accounts.json()["data"]["items"]
        self.assertTrue(items)
        self.assertEqual({item["tenant_id"] for item in items}, {data["tenant_id"]})
        foreign = self.client.get(f"{API}/accounts/2000000001", headers=headers, params={"bank_id": bank_b})
        self.assertEqual(foreign.status_code, 404, foreign.text)
        self.assertEqual(foreign.json()["internal_code"], "RESOURCE_NOT_FOUND")

    def test_public_login_respects_visibility_while_legacy_login_still_works(self):
        credentials = self.users[BANK_B][1]
        public_id = self.client.portal.call(self.configure_demo_bank, BANK_B, False)
        try:
            self.assertNotIn("Cooperativa Caribe Ltda.", self.directory())
            self.assert_authentication_failed(self.login({"bank_id": str(public_id)}, credentials, "203.0.113.61"))
            legacy = self.login({"tenant_slug": BANK_B}, credentials, "203.0.113.61")
            self.assertEqual(legacy.status_code, 200, legacy.text)
        finally:
            self.client.portal.call(self.configure_demo_bank, BANK_B, True)
        self.assertEqual(self.directory()["Cooperativa Caribe Ltda."]["bank_id"], str(public_id))

    def test_bank_id_and_slug_share_login_rate_limit(self):
        bank_id = self.directory()["Banco Andino S.A."]["bank_id"]
        credentials = {"email": f"directory-{uuid.uuid4().hex}@example.com", "password": "incorrecta"}
        for number in range(5):
            selector = {"bank_id": bank_id} if number % 2 == 0 else {"tenant_slug": BANK_A}
            self.assert_authentication_failed(self.login(selector, credentials, f"203.0.113.{70 + number}"))
        limited = self.login({"bank_id": bank_id.upper()}, credentials, "203.0.113.75")
        self.assertEqual(limited.status_code, 429, limited.text)
        self.assertEqual(limited.json()["internal_code"], "RATE_LIMIT_EXCEEDED")
        self.assertIn("Retry-After", limited.headers)
