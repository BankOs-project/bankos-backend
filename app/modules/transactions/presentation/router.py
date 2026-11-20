"""
Router de operaciones financieras de BankOS.

Los endpoints mutables (deposit, withdraw, transfer) usan la dependencia
`get_idempotency_context` que:
  - Exige el header Idempotency-Key (HTTP 400 si falta)
  - Bloquea concurrencia en PostgreSQL (HTTP 409 si está en proceso)
  - Confirma el comprobante y el movimiento en el mismo commit
  - Cachea en Redis la respuesta confirmada
"""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import BankOSException
from app.core.middlewares.idempotency import (
    IDEMPOTENCY_WINDOW,
    IdempotencyContext,
    get_idempotency_context,
)
from app.core.middlewares.rate_limit import enforce_transaction_user_limit
from app.core.responses import success_response
from app.modules.accounts.infrastructure.repositories import PostgresAccountRepository
from app.modules.auth_users.presentation.dependencies import (
    AuthenticatedUser,
    get_current_user,
)
from app.modules.tenants.infrastructure.repositories import PostgresTenantRepository
from app.modules.transactions.application.dtos import (
    DepositDTO,
    QuoteRequestDTO,
    TransactionResponseDTO,
    TransferDTO,
    WithdrawDTO,
)
from app.modules.transactions.application.use_cases import (
    DepositUseCase,
    GetTransactionHistoryUseCase,
    InternalTransferUseCase,
    QuoteTransactionUseCase,
    WithdrawUseCase,
    record_rejected_transaction,
    transaction_to_dto,
)
from app.modules.transactions.domain.entities import OperationType
from app.modules.transactions.infrastructure.repositories import (
    PostgresTransactionRepository,
)
from app.modules.transactions.infrastructure.webhook_dispatcher import HttpxWebhookDispatcher

router = APIRouter(
    prefix="/transactions",
    tags=["Operaciones Financieras"],
    dependencies=[Depends(enforce_transaction_user_limit)],
)


# ---------------------------------------------------------------------------
# Contexto de la solicitud y confirmación de operaciones
# ---------------------------------------------------------------------------

_SUCCESS_DETAILS = {
    OperationType.DEPOSIT: ("DEPOSIT_SUCCESS", "Depósito realizado exitosamente"),
    OperationType.WITHDRAWAL: ("WITHDRAWAL_SUCCESS", "Retiro realizado exitosamente"),
    OperationType.TRANSFER: ("TRANSFER_SUCCESS", "Transferencia realizada exitosamente"),
}


def _operation_response(result: TransactionResponseDTO) -> JSONResponse:
    code, message = _SUCCESS_DETAILS[result.operation_type]
    return success_response(
        data=result.model_dump(mode="json", exclude={"failure_code"}),
        message=message,
        internal_code=code,
        status_code=status.HTTP_201_CREATED,
    )


async def _replay_operation(
    idemp: IdempotencyContext,
    body_key: str | None,
    operation_type: OperationType,
) -> Response | None:
    replay = await idemp.replay_or_lock(body_key)
    if replay is not None:
        return replay

    legacy = await PostgresTransactionRepository(idemp.session).get_legacy_by_idempotency_key(
        idempotency_key=idemp.idempotency_key,
        tenant_id=idemp.user.tenant_id,
        actor_user_id=idemp.user.user_id,
        operation_type=operation_type,
        since=datetime.now(timezone.utc) - IDEMPOTENCY_WINDOW,
    )
    if legacy is None:
        return None
    return await idemp.confirm(
        _operation_response(transaction_to_dto(legacy)),
        expires_at=legacy.created_at + IDEMPOTENCY_WINDOW,
    )


@asynccontextmanager
async def _record_rejection(
    idemp: IdempotencyContext,
    operation_type: OperationType,
    amount: Decimal,
    *,
    source_account_number: str | None = None,
    destination_account_number: str | None = None,
):
    try:
        async with idemp.session.begin_nested():
            yield
    except BankOSException as exc:
        # El savepoint ya revirtió los cambios del movimiento. El intento
        # rechazado se confirma sin crear un comprobante de éxito.
        await record_rejected_transaction(
            PostgresAccountRepository(idemp.session),
            PostgresTransactionRepository(idemp.session),
            tenant_id=idemp.user.tenant_id,
            actor_user_id=idemp.user.user_id,
            actor_role=idemp.user.role,
            operation_type=operation_type,
            amount=amount,
            source_account_number=source_account_number,
            destination_account_number=destination_account_number,
            idempotency_key=idemp.idempotency_key,
            failure_code=exc.internal_code,
        )
        await idemp.session.commit()
        raise


# ---------------------------------------------------------------------------
# 1. Pre-cotización (no requiere Idempotency-Key, no modifica saldos)
# ---------------------------------------------------------------------------

@router.post(
    "/quote",
    summary="Pre-cotizar operación financiera (sin modificar saldos)",
)
async def quote_transaction(
    dto: QuoteRequestDTO,
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """Calcula el desglose exacto de comisiones, tasas de cambio y monto final
    antes de confirmar una operación. No toca ningún saldo bancario."""
    tenant_repo = PostgresTenantRepository(session)
    account_repo = PostgresAccountRepository(session)
    use_case = QuoteTransactionUseCase(tenant_repo, account_repo)

    result = await use_case.execute(
        tenant_id=current_user.tenant_id,
        dto=dto,
        actor_user_id=current_user.user_id,
        actor_role=current_user.role,
    )
    return success_response(
        data=result.model_dump(mode="json"),
        message="Cotización calculada exitosamente",
        internal_code="QUOTE_CALCULATED",
    )


# ---------------------------------------------------------------------------
# 2. Depósito (con idempotencia Redis)
# ---------------------------------------------------------------------------

@router.post(
    "/deposit",
    summary="Abonar fondos a una cuenta activa",
    status_code=status.HTTP_201_CREATED,
)
async def deposit(
    dto: DepositDTO,
    current_user: AuthenticatedUser = Depends(get_current_user),
    idemp: IdempotencyContext = Depends(get_idempotency_context),
):
    """Abona fondos a la cuenta indicada de forma atómica con garantía de idempotencia.

    Requiere el header **Idempotency-Key** (UUID v4 generado en el cliente).
    """
    replay = await _replay_operation(idemp, dto.idempotency_key, OperationType.DEPOSIT)
    if replay is not None:
        return replay

    session = idemp.session
    account_repo = PostgresAccountRepository(session)
    tx_repo = PostgresTransactionRepository(session)
    use_case = DepositUseCase(account_repo, tx_repo)

    async with _record_rejection(
        idemp, OperationType.DEPOSIT, dto.amount, destination_account_number=dto.account_number,
    ):
        result = await use_case.execute(
            tenant_id=current_user.tenant_id,
            dto=dto,
            actor_user_id=current_user.user_id,
            actor_role=current_user.role,
            idempotency_key=idemp.idempotency_key,
        )

    return await idemp.confirm(_operation_response(result))


# ---------------------------------------------------------------------------
# 3. Retiro (con idempotencia Redis)
# ---------------------------------------------------------------------------

@router.post(
    "/withdraw",
    summary="Retirar fondos de una cuenta activa del titular",
    status_code=status.HTTP_201_CREATED,
)
async def withdraw(
    dto: WithdrawDTO,
    current_user: AuthenticatedUser = Depends(get_current_user),
    idemp: IdempotencyContext = Depends(get_idempotency_context),
):
    """Debita fondos de la cuenta indicada validando saldo y titularidad.

    Requiere el header **Idempotency-Key** (UUID v4 generado en el cliente).
    """
    replay = await _replay_operation(idemp, dto.idempotency_key, OperationType.WITHDRAWAL)
    if replay is not None:
        return replay

    session = idemp.session
    account_repo = PostgresAccountRepository(session)
    tx_repo = PostgresTransactionRepository(session)
    use_case = WithdrawUseCase(account_repo, tx_repo)

    async with _record_rejection(
        idemp, OperationType.WITHDRAWAL, dto.amount, source_account_number=dto.account_number,
    ):
        result = await use_case.execute(
            tenant_id=current_user.tenant_id,
            dto=dto,
            actor_user_id=current_user.user_id,
            actor_role=current_user.role,
            idempotency_key=idemp.idempotency_key,
        )

    return await idemp.confirm(_operation_response(result))


# ---------------------------------------------------------------------------
# 4. Transferencia Interna (con idempotencia Redis y webhook asíncrono)
# ---------------------------------------------------------------------------

@router.post(
    "/transfer",
    summary="Transferencia interna multimoneda entre cuentas del mismo tenant",
    status_code=status.HTTP_201_CREATED,
)
async def transfer(
    dto: TransferDTO,
    background_tasks: BackgroundTasks,
    current_user: AuthenticatedUser = Depends(get_current_user),
    idemp: IdempotencyContext = Depends(get_idempotency_context),
):
    """Mueve fondos entre dos cuentas de la institución con:
    - Comisión configurada (FIXED o PERCENTAGE)
    - Conversión de divisas con tasas estáticas del tenant
    - Bloqueo pesimista anti-deadlock (orden alfabético por account_number)
    - Idempotencia por clave UUID con caché en Redis (TTL 24h)
    - Webhook asíncrono si el tenant tiene configurado `webhook_url`

    Requiere el header **Idempotency-Key** (UUID v4 generado en el cliente).
    """
    replay = await _replay_operation(idemp, dto.idempotency_key, OperationType.TRANSFER)
    if replay is not None:
        return replay

    session = idemp.session
    tenant_repo = PostgresTenantRepository(session)
    account_repo = PostgresAccountRepository(session)
    tx_repo = PostgresTransactionRepository(session)
    webhook_dispatcher = HttpxWebhookDispatcher()

    use_case = InternalTransferUseCase(
        tenant_repository=tenant_repo,
        account_repository=account_repo,
        transaction_repository=tx_repo,
        webhook_notifier=webhook_dispatcher,
    )

    async with _record_rejection(
        idemp, OperationType.TRANSFER, dto.amount,
        source_account_number=dto.source_account_number,
        destination_account_number=dto.destination_account_number,
    ):
        result = await use_case.execute(
            tenant_id=current_user.tenant_id,
            dto=dto,
            actor_user_id=current_user.user_id,
            actor_role=current_user.role,
            idempotency_key=idemp.idempotency_key,
        )

    tenant = await tenant_repo.get_by_id(current_user.tenant_id)
    response = await idemp.confirm(_operation_response(result))

    # Webhook en background (no bloquea la respuesta ni revierte la transacción)
    if tenant and tenant.webhook_url:
        background_tasks.add_task(
            webhook_dispatcher.dispatch,
            webhook_url=tenant.webhook_url,
            event_type="transfer.completed",
            payload=result.model_dump(mode="json", exclude={"failure_code"}),
            correlation_id=result.correlation_id,
        )

    return response


# ---------------------------------------------------------------------------
# 5. Historial paginado con filtros
# ---------------------------------------------------------------------------

@router.get(
    "",
    summary="Historial paginado de transacciones",
)
async def get_transaction_history(
    skip: int = Query(0, ge=0, description="Registros a omitir"),
    limit: int = Query(50, ge=1, le=100, description="Límite por página"),
    account_number: str | None = Query(None, description="Filtrar por número de cuenta"),
    operation_type: OperationType | None = Query(None, description="Filtrar por tipo de operación"),
    start_date: datetime | None = Query(None, description="Fecha de inicio del rango (ISO 8601)"),
    end_date: datetime | None = Query(None, description="Fecha de fin del rango (ISO 8601)"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """Consulta el historial de transacciones respetando el aislamiento por tenant_id y rol:
    - 'cliente': solo ve las operaciones de sus propias cuentas.
    - 'administrador': puede consultar todo el historial de la institución.
    """

    tx_repo = PostgresTransactionRepository(session)
    account_repo = PostgresAccountRepository(session)
    use_case = GetTransactionHistoryUseCase(tx_repo, account_repo)

    result = await use_case.execute(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        actor_role=current_user.role,
        skip=skip,
        limit=limit,
        account_number=account_number,
        operation_type=operation_type,
        start_date=start_date,
        end_date=end_date,
    )

    return success_response(
        data=result.model_dump(mode="json"),
        message="Historial de transacciones obtenido exitosamente",
        internal_code="TRANSACTIONS_FETCHED",
    )
