"""Cotización y ejecución de transferencias confirmadas con un UUID de un solo uso."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AuthorizationFailedException, BankOSException, ResourceNotFoundException
from app.core.middlewares.correlation_id import get_correlation_id
from app.core.middlewares.rate_limit import enforce_interbank_quote_limit
from app.modules.accounts.domain.entities import Account, AccountStatus
from app.modules.accounts.infrastructure.repositories import PostgresAccountRepository
from app.modules.auth_users.domain.entities import UserRole
from app.modules.auth_users.infrastructure.repositories import PostgresUserRepository
from app.modules.auth_users.presentation.dependencies import AuthenticatedUser
from app.modules.tenants.domain.entities import Tenant
from app.modules.tenants.infrastructure.repositories import PostgresTenantRepository
from app.modules.transactions.application.dtos import (
    QuoteRequestDTO, QuoteResponseDTO, RecipientPreviewDTO, TransactionResponseDTO, TransferQuoteDTO,
)
from app.modules.transactions.application.use_cases import transaction_to_dto, transfer_terms
from app.modules.transactions.domain.entities import OperationType, Transaction, TransferMode
from app.modules.transactions.infrastructure.models import TransferQuoteModel
from app.modules.transactions.infrastructure.repositories import PostgresTransactionRepository


def _unavailable_destination() -> BankOSException:
    return BankOSException("El destino no está disponible para recibir la transferencia", "DESTINATION_UNAVAILABLE", 404)


def _stale_quote() -> BankOSException:
    return BankOSException("Las condiciones cambiaron. Solicita una nueva cotización", "QUOTE_STALE", 409)


class QuotedTransferService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.accounts = PostgresAccountRepository(session)
        self.tenants = PostgresTenantRepository(session)
        self.users = PostgresUserRepository(session)
        self.transactions = PostgresTransactionRepository(session)

    @staticmethod
    def _check_source(source: Account, actor: AuthenticatedUser, mode: TransferMode) -> None:
        if source.owner_id != actor.user_id and (mode == TransferMode.OTHER_BANK or actor.role == UserRole.CLIENT):
            raise AuthorizationFailedException("La cuenta de origen debe pertenecer al remitente")
        source.ensure_can_operate()

    async def _recipient_preview(self, bank: Tenant, destination: Account | None) -> RecipientPreviewDTO:
        if destination is None or destination.status != AccountStatus.ACTIVE:
            raise _unavailable_destination()
        owner_name = await self.users.get_active_holder_name(destination.owner_id, bank.id)
        if owner_name is None:
            raise _unavailable_destination()
        words = owner_name.split()
        return RecipientPreviewDTO(
            bank_name=bank.name,
            masked_holder_name=" ".join(f"{word[0]}***" for word in words[:3]) or "***",
            account_last_four=destination.account_number[-4:],
        )

    async def quote(self, dto: QuoteRequestDTO, actor: AuthenticatedUser) -> TransferQuoteDTO:
        mode = dto.transfer_mode or TransferMode.SAME_BANK
        if mode == TransferMode.OTHER_BANK:
            if not settings.SIMULATED_INTERBANK_ENABLED:
                raise BankOSException("La transferencia simulada entre bancos está deshabilitada", "INTERBANK_DISABLED", 403)
            await enforce_interbank_quote_limit(actor.tenant_id, actor.user_id)

        tenant = await self.tenants.get_by_id(actor.tenant_id)
        if tenant is None or not tenant.is_active:
            raise ResourceNotFoundException("Banco de origen no disponible")
        source = await self.accounts.get_by_account_number(dto.source_account_number, tenant.id)
        if source is None:
            raise ResourceNotFoundException("Cuenta de origen no encontrada")
        self._check_source(source, actor, mode)

        destination_bank = tenant
        if mode == TransferMode.OTHER_BANK:
            destination_bank = await self.tenants.get_by_public_id(dto.destination_bank_id)
            if destination_bank is None or destination_bank.id == tenant.id:
                raise _unavailable_destination()
        destination = await self.accounts.get_by_account_number(dto.destination_account_number, destination_bank.id)
        if destination is None:
            raise _unavailable_destination()
        recipient = None
        if mode == TransferMode.OTHER_BANK:
            recipient = await self._recipient_preview(destination_bank, destination)
        else:
            destination.ensure_can_operate()

        try:
            terms = transfer_terms(tenant, source, destination, dto.amount, mode)
        except BankOSException as exc:
            if mode == TransferMode.OTHER_BANK and exc.internal_code == "EXCHANGE_RATE_UNAVAILABLE":
                raise _unavailable_destination() from None
            raise
        result = TransferQuoteDTO(
            **terms.model_dump(),
            quote_id=uuid.uuid4(),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            transfer_mode=mode,
            recipient=recipient,
        )
        self.session.add(TransferQuoteModel(
            id=result.quote_id,
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            source_id=source.id,
            source_number=source.account_number,
            destination_tenant_id=destination.tenant_id,
            destination_public_id=dto.destination_bank_id,
            destination_id=destination.id,
            destination_owner_id=destination.owner_id,
            destination_number=destination.account_number,
            snapshot=result.model_dump(mode="json"),
            expires_at=result.expires_at,
        ))
        await self.session.commit()
        return result

    async def get_locked_quote(self, quote_id: uuid.UUID, actor: AuthenticatedUser) -> TransferQuoteModel:
        quote = await self.session.scalar(select(TransferQuoteModel).where(
            TransferQuoteModel.id == quote_id,
            TransferQuoteModel.tenant_id == actor.tenant_id,
            TransferQuoteModel.user_id == actor.user_id,
        ).with_for_update())
        if quote is None:
            raise BankOSException("Cotización no disponible", "QUOTE_UNAVAILABLE", 404)
        if quote.consumed_at is not None:
            raise BankOSException("La cotización ya fue confirmada", "QUOTE_ALREADY_USED", 409)
        if quote.invalidated_at is not None:
            raise _stale_quote()
        if quote.expires_at <= datetime.now(timezone.utc):
            raise BankOSException("La cotización venció. Solicita otra", "QUOTE_EXPIRED", 409)
        return quote

    async def execute(
        self, quote: TransferQuoteModel, actor: AuthenticatedUser, idempotency_key: str,
    ) -> TransactionResponseDTO:
        expected = TransferQuoteDTO.model_validate(quote.snapshot)
        mode = expected.transfer_mode
        interbank = mode == TransferMode.OTHER_BANK
        if interbank and not settings.SIMULATED_INTERBANK_ENABLED:
            raise _stale_quote()

        # Todas las confirmaciones bloquean bancos y cuentas en un orden estable.
        bank_ids = sorted({quote.tenant_id, quote.destination_tenant_id})
        banks = {key: await self.tenants.get_by_id(key, for_update=True) for key in bank_ids}
        tenant = banks[quote.tenant_id]
        if tenant is None or not tenant.is_active:
            raise _stale_quote()
        destination_bank = tenant
        if interbank:
            destination_bank = await self.tenants.get_by_public_id(quote.destination_public_id)
            if destination_bank is None or destination_bank.id != quote.destination_tenant_id:
                raise _unavailable_destination()

        origin_key = (quote.tenant_id, quote.source_number)
        destination_key = (quote.destination_tenant_id, quote.destination_number)
        accounts = {key: await self.accounts.get_for_update(key[1], key[0]) for key in sorted((origin_key, destination_key))}
        source, destination = accounts[origin_key], accounts[destination_key]
        if source is None or source.id != quote.source_id:
            raise _stale_quote()
        self._check_source(source, actor, mode)
        if destination is None or destination.id != quote.destination_id or destination.owner_id != quote.destination_owner_id:
            raise _unavailable_destination()
        if interbank:
            if await self._recipient_preview(destination_bank, destination) != expected.recipient:
                raise _stale_quote()
        else:
            destination.ensure_can_operate()

        if quote.expires_at <= datetime.now(timezone.utc):
            raise BankOSException("La cotización venció. Solicita otra", "QUOTE_EXPIRED", 409)
        try:
            terms = transfer_terms(tenant, source, destination, expected.original_amount, mode)
        except BankOSException:
            raise _stale_quote() from None
        if terms != QuoteResponseDTO.model_validate(expected.model_dump()):
            raise _stale_quote()

        source.debit(terms.amount_to_debit)
        if destination.balance + terms.final_amount_to_receive >= Decimal("100000000000000"):
            raise _unavailable_destination()
        destination.credit(terms.final_amount_to_receive)
        await self.accounts.update(source)
        await self.accounts.update(destination)

        reference = uuid.uuid4()
        outgoing = await self.transactions.create(Transaction.create(
            tenant_id=source.tenant_id,
            operation_type=OperationType.TRANSFER,
            original_amount=terms.original_amount,
            original_currency=source.currency,
            converted_amount=terms.final_amount_to_receive,
            destination_currency=destination.currency,
            exchange_rate_applied=terms.exchange_rate_applied,
            fee_charged=terms.fee_calculated,
            idempotency_key=idempotency_key,
            correlation_id=get_correlation_id() or str(uuid.uuid4()),
            source_account_number=source.account_number,
            destination_account_number=None if interbank else destination.account_number,
            actor_user_id=actor.user_id,
            transfer_mode=mode,
            transfer_reference=reference,
        ))
        if interbank:
            await self.transactions.create(Transaction.create(
                tenant_id=destination.tenant_id,
                operation_type=OperationType.TRANSFER,
                original_amount=terms.final_amount_to_receive,
                original_currency=destination.currency,
                converted_amount=terms.final_amount_to_receive,
                destination_currency=destination.currency,
                exchange_rate_applied=Decimal("1"),
                fee_charged=Decimal("0"),
                idempotency_key=str(uuid.uuid4()),
                correlation_id=str(uuid.uuid4()),
                destination_account_number=destination.account_number,
                transfer_mode=mode,
                transfer_reference=reference,
            ))
        quote.consumed_at = datetime.now(timezone.utc)
        return transaction_to_dto(outgoing)
