"""Contrato del perfil propio con PostgreSQL, Redis y entrega de correo simulada."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from io import BytesIO
import os
import unittest
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin
from sqlalchemy import text

from demo_seed_fixture import load_demo_seed


ME = "/api/v1/auth/me"
PHOTO = f"{ME}/photo"
EMAIL = f"{ME}/email-change"
PASSWORD = "Perfil-Pruebas-13!"
LEGACY_FIELDS = {"id", "tenant_id", "email", "full_name", "role", "is_active", "created_at"}
OPTIONAL_FIELDS = {"document_type", "document_number", "phone", "address", "photo_url", "email_verified_at"}


def png(width=2):
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Comment", "Metadato privado de prueba")
    buffer = BytesIO()
    Image.new("RGBA", (width, 3), (0, 50, 200, 75)).save(buffer, "PNG", pnginfo=metadata)
    return buffer.getvalue()


@unittest.skipUnless(
    os.getenv("BANKOS_TEST_DATABASE_URL") and os.getenv("BANKOS_TEST_REDIS_URL"),
    "Requiere PostgreSQL y Redis desechables",
)
class ProfileTest(unittest.TestCase):
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
        from app.core.security import create_access_token, get_password_hash
        from app.main import app

        cls.session_factory = async_session_factory
        cls.make_token = staticmethod(create_access_token)
        cls.password_hash = get_password_hash(PASSWORD)
        cls.mail = stack.enter_context(patch(
            "app.modules.auth_users.application.profile.send_profile_email_code", new_callable=AsyncMock,
        ))

        async def with_client_ip(scope, receive, send):
            peer = dict(scope.get("headers", [])).get(b"x-test-peer")
            if peer:
                scope = {**scope, "client": (peer.decode(), 1234)}
            await app(scope, receive, send)

        cls.client = stack.enter_context(TestClient(with_client_ip))
        cls.client.portal.call(load_demo_seed)

    def setUp(self):
        self.mail.reset_mock()
        self.person_id = uuid4()
        self.user_a, self.user_b = uuid4(), uuid4()
        self.slug_a, self.slug_b = "banco-andino", "cooperativa-caribe"
        self.email_a = f"perfil-{self.user_a.hex}@example.com"
        self.email_b = f"perfil-{self.user_b.hex}@example.com"
        self.peer = {"X-Test-Peer": f"2001:db8::{self.user_a.hex[:4]}:{self.user_a.hex[4:8]}"}
        self.client.portal.call(self.create_users)
        self.a = self.login(self.slug_a, self.email_a)
        self.b = self.login(self.slug_b, self.email_b)
        self.headers_a = {"Authorization": f"Bearer {self.a['access_token']}"}
        self.headers_b = {"Authorization": f"Bearer {self.b['access_token']}"}

    async def create_users(self):
        async with self.session_factory.begin() as session:
            await session.execute(text("INSERT INTO persons (id) VALUES (:id)"), {"id": self.person_id})
            for slug, user, email in (
                (self.slug_a, self.user_a, self.email_a), (self.slug_b, self.user_b, self.email_b),
            ):
                await session.execute(text("""
                    INSERT INTO users (id, tenant_id, person_id, email, hashed_password, full_name,
                                       role, is_active, created_at, updated_at)
                    SELECT :user, id, :person, :email, :hash, 'Cliente de prueba', 'cliente', true, now(), now()
                    FROM tenants WHERE slug = :slug
                """), {"user": user, "person": self.person_id, "email": email, "hash": self.password_hash, "slug": slug})

    def check(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.json()["internal_code"], code)
        self.assertNotIn("person_id", response.text)
        return response.json()["data"]

    def login(self, slug, email, *, succeeds=True):
        response = self.client.post("/api/v1/auth/login", headers=self.peer, json={
            "tenant_slug": slug, "email": email, "password": PASSWORD,
        })
        if succeeds:
            return self.check(response, 200, "LOGIN_SUCCESS")
        return self.check(response, 401, "AUTHENTICATION_FAILED")

    def test_profile_updates_and_clears_optional_fields_with_legacy_session(self):
        original = self.check(self.client.get(ME, headers=self.headers_a), 200, "USER_PROFILE")
        self.assertEqual(set(original), LEGACY_FIELDS | OPTIONAL_FIELDS)
        self.assertTrue(all(original[field] is None for field in OPTIONAL_FIELDS))
        self.check(self.client.patch(ME, headers=self.headers_a, json={
            "document_type": "CC", "full_name": "No se debe guardar",
        }), 422, "PROFILE_DOCUMENT_INCOMPLETE")
        self.check(self.client.patch(ME, headers=self.headers_a, json={"full_name": None}), 422, "VALIDATION_ERROR")
        self.check(self.client.patch(ME, headers=self.headers_a, json={
            "document_type": "CE", "document_number": 12345,
        }), 422, "VALIDATION_ERROR")

        fields = {"full_name": "  Ana María Ficticia  ", "document_type": "CC", "document_number": "001234567",
                  "phone": "+573001234567", "address": "Calle privada de prueba"}
        updated = self.check(self.client.patch(ME, headers=self.headers_a, json=fields), 200, "USER_PROFILE_UPDATED")
        self.assertEqual(updated["full_name"], fields["full_name"].strip())
        for field in ("document_type", "document_number", "phone", "address"):
            self.assertEqual(updated[field], fields[field])
        for field in LEGACY_FIELDS - {"full_name"}:
            self.assertEqual(updated[field], original[field])
        current = self.client.get(ME, headers=self.headers_a)
        self.assertEqual(current.headers["Cache-Control"], "private, no-store")
        self.assertEqual(self.check(current, 200, "USER_PROFILE"), updated)
        other = self.check(self.client.get(ME, headers=self.headers_b), 200, "USER_PROFILE")
        self.assertTrue(all(other[field] is None for field in OPTIONAL_FIELDS))

        cleared = self.check(self.client.patch(ME, headers=self.headers_a, json={
            "document_type": None, "document_number": "", "phone": "  ", "address": None,
        }), 200, "USER_PROFILE_UPDATED")
        self.assertTrue(all(cleared[field] is None for field in OPTIONAL_FIELDS))
        self.assertEqual(cleared["full_name"], updated["full_name"])

        async def people():
            async with self.session_factory() as session:
                return list(await session.scalars(text("SELECT person_id FROM users WHERE id IN (:a, :b)"), {
                    "a": self.user_a, "b": self.user_b,
                }))

        self.assertEqual(self.client.portal.call(people), [self.person_id, self.person_id])

    def test_photo_is_private_normalized_and_removable(self):
        headers = {**self.headers_a, "Content-Type": "image/png"}
        photo = png()
        self.check(self.client.put(PHOTO, headers={**headers, "Content-Type": "image/svg+xml"}, content=photo),
                   415, "PROFILE_PHOTO_UNSUPPORTED_TYPE")
        self.check(self.client.put(PHOTO, headers={**headers, "Content-Type": "image/jpeg"}, content=photo),
                   422, "PROFILE_PHOTO_INVALID")
        self.check(self.client.put(PHOTO, headers=headers, content=png(2049)), 422, "PROFILE_PHOTO_INVALID")
        self.check(self.client.put(PHOTO, headers=headers, content=b"x" * (2 * 1024 * 1024 + 1)),
                   413, "PROFILE_PHOTO_TOO_LARGE")
        uploaded = self.check(self.client.put(PHOTO, headers=headers, content=photo), 200, "PROFILE_PHOTO_UPDATED")
        self.assertEqual(uploaded["photo_url"], PHOTO)
        response = self.client.get(PHOTO, headers=self.headers_a)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "image/png")
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        with Image.open(BytesIO(response.content)) as image:
            self.assertEqual(image.getpixel((0, 0)), (0, 50, 200, 75))
            self.assertNotIn("Comment", image.info)
        self.check(self.client.get(PHOTO, headers=self.headers_b), 404, "PROFILE_PHOTO_NOT_FOUND")
        self.check(self.client.get(PHOTO), 401, "UNAUTHORIZED")
        for _ in range(2):
            profile = self.check(self.client.delete(PHOTO, headers=self.headers_a), 200, "PROFILE_PHOTO_REMOVED")
            self.assertIsNone(profile["photo_url"])
        self.check(self.client.get(PHOTO, headers=self.headers_a), 404, "PROFILE_PHOTO_NOT_FOUND")

    def test_invalid_tenant_claim_cannot_access_any_profile_operation(self):
        token = self.make_token(self.b["tenant_id"], str(self.user_a), "cliente")
        headers = {"Authorization": f"Bearer {token}"}
        for method, path, arguments in (
            ("GET", ME, {}), ("PATCH", ME, {"json": {"address": "Dirección ajena"}}), ("GET", PHOTO, {}),
            ("PUT", PHOTO, {"content": png(), "headers": {**headers, "Content-Type": "image/png"}}),
            ("DELETE", PHOTO, {}), ("POST", EMAIL, {"json": {
                "new_email": f"ajeno-{uuid4().hex}@example.com", "current_password": PASSWORD,
            }}), ("POST", f"{EMAIL}/verify", {"json": {"request_id": str(uuid4()), "email_code": "falso"}}),
        ):
            arguments.setdefault("headers", headers)
            with self.subTest(method=method, path=path):
                self.check(self.client.request(method, path, **arguments), 401, "AUTHENTICATION_FAILED")
        self.check(self.client.patch(ME, headers=self.headers_a, json={"tenant_id": self.b["tenant_id"]}),
                   422, "VALIDATION_ERROR")
        self.mail.assert_not_awaited()

    def start_email(self, new_email):
        response = self.client.post(EMAIL, headers=self.headers_a, json={"new_email": new_email, "current_password": PASSWORD})
        pending = self.check(response, 202, "PROFILE_EMAIL_PENDING")
        mail_address, code = self.mail.await_args.args
        self.assertEqual(mail_address, new_email.lower())
        self.assertEqual(pending["expires_in"], 1200)
        self.assertNotIn(code, response.text)
        return {"request_id": pending["request_id"], "email_code": code}

    def test_email_switch_requires_new_mailbox_and_consumes_verification_once(self):
        self.check(self.client.patch(ME, headers=self.headers_a, json={"email": self.email_b}), 422, "VALIDATION_ERROR")
        self.check(self.client.post(EMAIL, headers=self.headers_a, json={
            "new_email": self.email_b, "current_password": "contraseña incorrecta",
        }), 401, "AUTHENTICATION_FAILED")
        self.mail.assert_not_awaited()
        verification = self.start_email(self.email_b.upper())
        self.login(self.slug_a, self.email_a)
        self.login(self.slug_a, self.email_b, succeeds=False)
        self.check(self.client.post(f"{EMAIL}/verify", headers=self.headers_b, json=verification),
                   400, "PROFILE_EMAIL_INVALID")
        self.check(self.client.post(f"{EMAIL}/verify", headers=self.headers_a, json={**verification, "email_code": "falso"}),
                   400, "PROFILE_EMAIL_INVALID")

        def verify():
            return self.client.post(f"{EMAIL}/verify", headers=self.headers_a, json=verification)

        with ThreadPoolExecutor(max_workers=2) as workers:
            responses = [workers.submit(verify), workers.submit(verify)]
            success, replay = sorted((future.result() for future in responses), key=lambda response: response.status_code)
        updated = self.check(success, 200, "PROFILE_EMAIL_UPDATED")
        self.check(replay, 400, "PROFILE_EMAIL_INVALID")
        self.assertEqual(updated["email"], self.email_b)
        self.assertIsNotNone(updated["email_verified_at"])
        self.assertEqual(updated["id"], str(self.user_a))
        self.login(self.slug_a, self.email_a, succeeds=False)
        self.assertEqual(self.login(self.slug_a, self.email_b)["user_id"], str(self.user_a))
        self.assertEqual(self.login(self.slug_b, self.email_b)["user_id"], str(self.user_b))
        self.assertEqual(self.check(self.client.get(ME, headers=self.headers_a), 200, "USER_PROFILE"), updated)

    def test_email_expiry_and_same_bank_conflict_preserve_old_login(self):
        expired = self.start_email(f"expirado-{uuid4().hex}@example.com")

        async def expire():
            async with self.session_factory.begin() as session:
                await session.execute(text("""
                    UPDATE profile_email_changes SET expires_at = now() - interval '1 second' WHERE request_id = :id
                """), {"id": UUID(expired["request_id"])})

        self.client.portal.call(expire)
        self.check(self.client.post(f"{EMAIL}/verify", headers=self.headers_a, json=expired), 400, "PROFILE_EMAIL_INVALID")

        from scripts.seed_demo import USERS

        duplicate = self.start_email(USERS[self.slug_a][0]["email"])
        self.check(self.client.post(f"{EMAIL}/verify", headers=self.headers_a, json=duplicate),
                   409, "PROFILE_EMAIL_UNAVAILABLE_IN_BANK")
        self.assertEqual(self.login(self.slug_a, self.email_a)["user_id"], str(self.user_a))
