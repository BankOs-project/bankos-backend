from datetime import datetime
from decimal import Decimal
import uuid

from app.core.exceptions import (
    AuthorizationFailedException,
    BankOSException,
    ResourceNotFoundException,
    TenantNotFoundException,
)
from app.core.middlewares.correlation_id import get_correlation_id
from app.modules.accounts.domain.entities import Account
from app.modules.accounts.domain.repositories import AccountRepository
from app.modules.auth_users.domain.entities import UserRole
from app.modules.tenants.domain.repositories import TenantRepository
from app.modules.transactions.application.dtos import (
    DepositDTO,
    PaginatedTransactionsResponseDTO,
    QuoteRequestDTO,
    QuoteResponseDTO,
    TransactionResponseDTO,
    TransferDTO,
    WithdrawDTO,
)
from app.modules.transactions.domain.currency_service import (
    apply_conversion,
    calculate_fee,
    get_exchange_rate,
)
from app.modules.transactions.domain.currency_service import ONE as ONE  # noqa: F811
from app.modules.transactions.domain.entities import (
    OperationType,
    Transaction,
    TransactionQuote,
    TransactionStatus,
)
from app.modules.transactions.domain.repositories import (
    TransactionRepository,
    WebhookNotifierPort,
)

# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _tx_to_dto(tx: Transaction) -> TransactionResponseDTO:
    return TransactionResponseDTO(
        id=tx.id,
        tenant_id=tx.tenant_id,
        operation_type=tx.operation_type,
        source_account_number=tx.source_account_number,
        destination_account_number=tx.destination_account_number,
        original_amount=tx.original_amount,
        original_currency=tx.original_currency,
        converted_amount=tx.converted_amount,
        destination_currency=tx.destination_currency,
        exchange_rate_applied=tx.exchange_rate_applied,
        fee_charged=tx.fee_charged,
        status=tx.status,
        idempotency_key=tx.idempotency_key,
        correlation_id=tx.correlation_id,
        created_at=tx.created_at,
    )


# ---------------------------------------------------------------------------
# 1. Pre-cotización
# ---------------------------------------------------------------------------

class QuoteTransactionUseCase:
    """Calcula el desglose exacto de una operación sin modificar saldos."""

    def __init__(
        self,
        tenant_repository: TenantRepository,
        account_repository: AccountRepository,
    ) -> None:
        self.tenant_repository = tenant_repository
        self.account_repository = account_repository

    async def execute(
        self,
        tenant_id: uuid.UUID,
        dto: QuoteRequestDTO,
    ) -> QuoteResponseDTO:
        tenant = await self.tenant_repository.get_by_id(tenant_id)
        if not tenant or not tenant.is_active:
            raise TenantNotFoundException()

        source = await self.account_repository.get_by_account_number(
            account_number=dto.source_account_number,
            tenant_id=tenant_id,
        )
        if not source:
            raise ResourceNotFoundException(
                f"Cuenta origen '{dto.source_account_number}' no encontrada en esta institución"
            )
        source.ensure_can_operate()

        amount = Decimal(str(dto.amount))
        source_currency = source.currency
        dest_currency = source_currency
        exchange_rate = ONE

        if dto.operation_type == OperationType.TRANSFER and dto.destination_account_number:
            dest_acc = await self.account_repository.get_by_account_number(
                account_number=dto.destination_account_number,
                tenant_id=tenant_id,
            )
            if not dest_acc:
                raise ResourceNotFoundException(
                    f"Cuenta destino '{dto.destination_account_number}' no encontrada"
                )
            dest_acc.ensure_can_operate()
            dest_currency = dest_acc.currency
            exchange_rate = get_exchange_rate(source_currency, dest_currency, tenant)

        fee = calculate_fee(amount, tenant) if dto.operation_type == OperationType.TRANSFER else Decimal("0.0000")
        amount_to_debit = amount + fee
        final_amount = apply_conversion(amount, exchange_rate)

        return QuoteResponseDTO(
            operation_type=dto.operation_type,
            source_currency=source_currency,
            destination_currency=dest_currency,
            original_amount=amount,
            fee_calculated=fee,
            amount_to_debit=amount_to_debit,
            exchange_rate_applied=exchange_rate,
            final_amount_to_receive=final_amount,
        )


# ---------------------------------------------------------------------------
# 2. Depósito
# ---------------------------------------------------------------------------

class DepositUseCase:
    """Abona fondos de forma atómica a una cuenta activa del tenant."""

    def __init__(
        self,
        account_repository: AccountRepository,
        transaction_repository: TransactionRepository,
    ) -> None:
        self.account_repository = account_repository
        self.transaction_repository = transaction_repository

    async def execute(
        self,
        tenant_id: uuid.UUID,
        dto: DepositDTO,
        actor_user_id: uuid.UUID,
        actor_role: UserRole,
    ) -> TransactionResponseDTO:
        correlation_id = get_correlation_id() or str(uuid.uuid4())

        # 1. Idempotencia: si ya existe, retornar la respuesta original
        existing = await self.transaction_repository.get_by_idempotency_key(
            idempotency_key=dto.idempotency_key,
            tenant_id=tenant_id,
        )
        if existing:
            return _tx_to_dto(existing)

        # 2. Bloqueo pesimista sobre la cuenta destino
        account = await self.account_repository.get_for_update(
            account_number=dto.account_number,
            tenant_id=tenant_id,
        )
        if not account:
            raise ResourceNotFoundException(
                f"Cuenta '{dto.account_number}' no encontrada en esta institución"
            )

        # 3. Validación de autorización: cliente solo puede depositar en sus propias cuentas
        if actor_role == UserRole.CLIENT and account.owner_id != actor_user_id:
            raise AuthorizationFailedException(
                "No posee autorización para depositar en una cuenta de la cual no es titular"
            )

        amount = Decimal(str(dto.amount))

        # 4. Regla de dominio: cuenta activa + monto positivo (validado en entidad)
        account.credit(amount)
        await self.account_repository.update(account)

        # 5. Persistir registro de transacción
        tx = Transaction.create(
            tenant_id=tenant_id,
            operation_type=OperationType.DEPOSIT,
            original_amount=amount,
            original_currency=account.currency,
            converted_amount=amount,
            destination_currency=account.currency,
            exchange_rate_applied=ONE,
            fee_charged=Decimal("0.0000"),
            idempotency_key=dto.idempotency_key,
            correlation_id=correlation_id,
            source_account_number=None,
            destination_account_number=dto.account_number,
            status=TransactionStatus.SUCCESS,
        )
        saved = await self.transaction_repository.create(tx)
        return _tx_to_dto(saved)


# ---------------------------------------------------------------------------
# 3. Retiro
# ---------------------------------------------------------------------------

class WithdrawUseCase:
    """Debita fondos de forma atómica de una cuenta activa del titular."""

    def __init__(
        self,
        account_repository: AccountRepository,
        transaction_repository: TransactionRepository,
    ) -> None:
        self.account_repository = account_repository
        self.transaction_repository = transaction_repository

    async def execute(
        self,
        tenant_id: uuid.UUID,
        dto: WithdrawDTO,
        actor_user_id: uuid.UUID,
        actor_role: UserRole,
    ) -> TransactionResponseDTO:
        correlation_id = get_correlation_id() or str(uuid.uuid4())

        # 1. Idempotencia
        existing = await self.transaction_repository.get_by_idempotency_key(
            idempotency_key=dto.idempotency_key,
            tenant_id=tenant_id,
        )
        if existing:
            return _tx_to_dto(existing)

        # 2. Bloqueo pesimista
        account = await self.account_repository.get_for_update(
            account_number=dto.account_number,
            tenant_id=tenant_id,
        )
        if not account:
            raise ResourceNotFoundException(
                f"Cuenta '{dto.account_number}' no encontrada en esta institución"
            )

        # 3. Verificar titularidad: clientes solo pueden retirar de sus cuentas propias
        if actor_role == UserRole.CLIENT and account.owner_id != actor_user_id:
            raise AuthorizationFailedException(
                "No posee autorización para retirar de una cuenta de la cual no es titular"
            )

        amount = Decimal(str(dto.amount))

        # 4. Regla de dominio: valida activa + saldo suficiente
        account.debit(amount)
        await self.account_repository.update(account)

        # 5. Persistir transacción
        tx = Transaction.create(
            tenant_id=tenant_id,
            operation_type=OperationType.WITHDRAWAL,
            original_amount=amount,
            original_currency=account.currency,
            converted_amount=amount,
            destination_currency=account.currency,
            exchange_rate_applied=ONE,
            fee_charged=Decimal("0.0000"),
            idempotency_key=dto.idempotency_key,
            correlation_id=correlation_id,
            source_account_number=dto.account_number,
            destination_account_number=None,
            status=TransactionStatus.SUCCESS,
        )
        saved = await self.transaction_repository.create(tx)
        return _tx_to_dto(saved)


# ---------------------------------------------------------------------------
# 4. Transferencia Interna
# ---------------------------------------------------------------------------

class InternalTransferUseCase:
    """Transfiere fondos entre dos cuentas del mismo tenant con atomicidad ACID completa."""

    def __init__(
        self,
        tenant_repository: TenantRepository,
        account_repository: AccountRepository,
        transaction_repository: TransactionRepository,
        webhook_notifier: WebhookNotifierPort | None = None,
    ) -> None:
        self.tenant_repository = tenant_repository
        self.account_repository = account_repository
        self.transaction_repository = transaction_repository
        self.webhook_notifier = webhook_notifier

    async def execute(
        self,
        tenant_id: uuid.UUID,
        dto: TransferDTO,
        actor_user_id: uuid.UUID,
        actor_role: UserRole,
    ) -> TransactionResponseDTO:
        correlation_id = get_correlation_id() or str(uuid.uuid4())

        # 1. Idempotencia: retornar respuesta original si la clave ya fue procesada
        existing = await self.transaction_repository.get_by_idempotency_key(
            idempotency_key=dto.idempotency_key,
            tenant_id=tenant_id,
        )
        if existing:
            return _tx_to_dto(existing)

        # 2. Cargar configuración del tenant (comisiones, límites, tasas de cambio)
        tenant = await self.tenant_repository.get_by_id(tenant_id)
        if not tenant or not tenant.is_active:
            raise TenantNotFoundException()

        # 3. Bloqueo pesimista anti-deadlock: ordenar cuentas ALFABÉTICAMENTE por account_number
        #    Esto garantiza que dos transferencias concurrentes entre las mismas cuentas
        #    adquieran los locks siempre en el mismo orden, evitando deadlocks en PostgreSQL.
        ordered_accounts = sorted([
            dto.source_account_number,
            dto.destination_account_number,
        ])

        locked: dict[str, Account] = {}
        for acc_num in ordered_accounts:
            acc = await self.account_repository.get_for_update(
                account_number=acc_num,
                tenant_id=tenant_id,
            )
            if not acc:
                raise ResourceNotFoundException(
                    f"Cuenta '{acc_num}' no encontrada en esta institución"
                )
            locked[acc_num] = acc

        source = locked[dto.source_account_number]
        destination = locked[dto.destination_account_number]

        # 4. Validar que ambas cuentas estén activas
        source.ensure_can_operate()
        destination.ensure_can_operate()

        # 5. Validar autorización: el cliente solo puede transferir desde sus propias cuentas
        if actor_role == UserRole.CLIENT and source.owner_id != actor_user_id:
            raise AuthorizationFailedException(
                "No posee autorización para transferir desde una cuenta de la cual no es titular"
            )

        amount = Decimal(str(dto.amount))

        # 6. Validar límite máximo de transacción del tenant
        if amount > tenant.max_transaction_limit:
            raise BankOSException(
                message=(
                    f"El monto de la transferencia ({amount} {source.currency}) supera el límite "
                    f"máximo permitido por la institución ({tenant.max_transaction_limit} {source.currency})"
                ),
                internal_code="TRANSACTION_LIMIT_EXCEEDED",
                status_code=422,
            )

        # 7. Calcular comisión con Decimal puro
        fee = calculate_fee(amount, tenant)
        total_to_debit = amount + fee

        # 8. Verificar saldo suficiente para monto + comisión (antes del lock dominio)
        if source.balance < total_to_debit:
            from app.core.exceptions import InsufficientFundsException
            raise InsufficientFundsException(
                message=(
                    f"Saldo insuficiente. Se requieren {total_to_debit} {source.currency} "
                    f"(monto: {amount} + comisión: {fee}). "
                    f"Saldo disponible: {source.balance} {source.currency}"
                )
            )

        # 9. Calcular conversión de divisa si las cuentas tienen monedas distintas
        exchange_rate = get_exchange_rate(source.currency, destination.currency, tenant)
        converted_amount = apply_conversion(amount, exchange_rate)

        # 10. Aplicar movimientos en las entidades de dominio (incluye validación interna)
        source.debit(total_to_debit)
        destination.credit(converted_amount)

        # 11. Persistir cambios en base de datos dentro de la misma transacción ACID
        await self.account_repository.update(source)
        await self.account_repository.update(destination)

        # 12. Crear y persistir el registro de la transacción
        tx = Transaction.create(
            tenant_id=tenant_id,
            operation_type=OperationType.TRANSFER,
            original_amount=amount,
            original_currency=source.currency,
            converted_amount=converted_amount,
            destination_currency=destination.currency,
            exchange_rate_applied=exchange_rate,
            fee_charged=fee,
            idempotency_key=dto.idempotency_key,
            correlation_id=correlation_id,
            source_account_number=source.account_number,
            destination_account_number=destination.account_number,
            status=TransactionStatus.SUCCESS,
        )
        saved = await self.transaction_repository.create(tx)

        return _tx_to_dto(saved)


# ---------------------------------------------------------------------------
# 5. Historial paginado
# ---------------------------------------------------------------------------

class GetTransactionHistoryUseCase:
    """Lista el historial de transacciones del tenant con filtros y paginación."""

    def __init__(
        self,
        transaction_repository: TransactionRepository,
        account_repository: AccountRepository,
    ) -> None:
        self.transaction_repository = transaction_repository
        self.account_repository = account_repository

    async def execute(
        self,
        tenant_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_role: UserRole,
        skip: int = 0,
        limit: int = 50,
        account_number: str | None = None,
        operation_type: OperationType | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> PaginatedTransactionsResponseDTO:
        if actor_role == UserRole.CLIENT:
            # Obtener solo los account_numbers propios del cliente
            user_accounts, _ = await self.account_repository.list_by_owner(
                owner_id=actor_user_id,
                tenant_id=tenant_id,
                skip=0,
                limit=1000,
            )
            user_account_numbers = [a.account_number for a in user_accounts]

            # Si se especifica un account_number, verificar que pertenezca al cliente
            if account_number and account_number not in user_account_numbers:
                raise AuthorizationFailedException(
                    "No posee autorización para consultar el historial de esta cuenta"
                )

            filter_numbers = [account_number] if account_number else user_account_numbers

            txs, total = await self.transaction_repository.list_by_owner_accounts(
                tenant_id=tenant_id,
                account_numbers=filter_numbers,
                skip=skip,
                limit=limit,
                operation_type=operation_type,
                start_date=start_date,
                end_date=end_date,
            )
        else:
            # Administrador puede ver todo el historial del tenant
            txs, total = await self.transaction_repository.list_by_tenant(
                tenant_id=tenant_id,
                skip=skip,
                limit=limit,
                account_number=account_number,
                operation_type=operation_type,
                start_date=start_date,
                end_date=end_date,
            )

        return PaginatedTransactionsResponseDTO(
            total=total,
            skip=skip,
            limit=limit,
            items=[_tx_to_dto(tx) for tx in txs],
        )
