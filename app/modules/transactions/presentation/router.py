"""
Router de operaciones financieras de BankOS.

Los endpoints mutables (deposit, withdraw, transfer) usan la dependencia
`get_idempotency_context` que:
  - Exige el header Idempotency-Key (HTTP 400 si falta)
  - Bloquea concurrencia con SET NX en Redis (HTTP 409 si está en proceso)
  - Retorna la respuesta cacheada si ya fue completada (idempotencia real)
  - Proporciona `store_response()` para cachear el resultado final
"""
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.middlewares.idempotency import IdempotencyContext, get_idempotency_context
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
    TransferDTO,
    WithdrawDTO,
)
from app.modules.transactions.application.use_cases import (
    DepositUseCase,
    GetTransactionHistoryUseCase,
    InternalTransferUseCase,
    QuoteTransactionUseCase,
    WithdrawUseCase,
)
from app.modules.transactions.domain.entities import OperationType
from app.modules.transactions.infrastructure.repositories import (
    PostgresTransactionRepository,
)
from app.modules.transactions.infrastructure.webhook_dispatcher import HttpxWebhookDispatcher

router = APIRouter(prefix="/transactions", tags=["Operaciones Financieras"])


# ---------------------------------------------------------------------------
# Helper: propaga identidad a request.state para rate limiting e idempotencia
# ---------------------------------------------------------------------------

def _attach_identity_to_state(request: Request, current_user: AuthenticatedUser) -> None:
    """Escribe tenant_id y user_id en request.state para los middlewares."""
    request.state.tenant_id = str(current_user.tenant_id)
    request.state.user_id = str(current_user.user_id)


# ---------------------------------------------------------------------------
# 1. Pre-cotización (no requiere Idempotency-Key, no modifica saldos)
# ---------------------------------------------------------------------------

@router.post(
    "/quote",
    summary="Pre-cotizar operación financiera (sin modificar saldos)",
)
async def quote_transaction(
    dto: QuoteRequestDTO,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """Calcula el desglose exacto de comisiones, tasas de cambio y monto final
    antes de confirmar una operación. No toca ningún saldo bancario."""
    _attach_identity_to_state(request, current_user)
    tenant_repo = PostgresTenantRepository(session)
    account_repo = PostgresAccountRepository(session)
    use_case = QuoteTransactionUseCase(tenant_repo, account_repo)

    result = await use_case.execute(tenant_id=current_user.tenant_id, dto=dto)
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
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    idemp: IdempotencyContext = Depends(get_idempotency_context),
):
    """Abona fondos a la cuenta indicada de forma atómica con garantía de idempotencia.

    Requiere el header **Idempotency-Key** (UUID v4 generado en el cliente).
    """
    _attach_identity_to_state(request, current_user)

    # Respuesta cacheada → retornar sin re-ejecutar
    if idemp.cached_response is not None:
        return idemp.cached_response

    account_repo = PostgresAccountRepository(session)
    tx_repo = PostgresTransactionRepository(session)
    use_case = DepositUseCase(account_repo, tx_repo)

    result = await use_case.execute(
        tenant_id=current_user.tenant_id,
        dto=dto,
        actor_user_id=current_user.user_id,
        actor_role=current_user.role,
    )

    response_body = result.model_dump(mode="json")
    await idemp.store_response(status_code=201, body={
        "success": True,
        "internal_code": "DEPOSIT_SUCCESS",
        "message": "Depósito realizado exitosamente",
        "data": response_body,
    })

    return success_response(
        data=response_body,
        message="Depósito realizado exitosamente",
        internal_code="DEPOSIT_SUCCESS",
        status_code=status.HTTP_201_CREATED,
    )


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
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    idemp: IdempotencyContext = Depends(get_idempotency_context),
):
    """Debita fondos de la cuenta indicada validando saldo y titularidad.

    Requiere el header **Idempotency-Key** (UUID v4 generado en el cliente).
    """
    _attach_identity_to_state(request, current_user)

    if idemp.cached_response is not None:
        return idemp.cached_response

    account_repo = PostgresAccountRepository(session)
    tx_repo = PostgresTransactionRepository(session)
    use_case = WithdrawUseCase(account_repo, tx_repo)

    result = await use_case.execute(
        tenant_id=current_user.tenant_id,
        dto=dto,
        actor_user_id=current_user.user_id,
        actor_role=current_user.role,
    )

    response_body = result.model_dump(mode="json")
    await idemp.store_response(status_code=201, body={
        "success": True,
        "internal_code": "WITHDRAWAL_SUCCESS",
        "message": "Retiro realizado exitosamente",
        "data": response_body,
    })

    return success_response(
        data=response_body,
        message="Retiro realizado exitosamente",
        internal_code="WITHDRAWAL_SUCCESS",
        status_code=status.HTTP_201_CREATED,
    )


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
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
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
    _attach_identity_to_state(request, current_user)

    if idemp.cached_response is not None:
        return idemp.cached_response

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

    result = await use_case.execute(
        tenant_id=current_user.tenant_id,
        dto=dto,
        actor_user_id=current_user.user_id,
        actor_role=current_user.role,
    )

    response_body = result.model_dump(mode="json")

    # Cachear la respuesta en Redis antes de responder
    await idemp.store_response(status_code=201, body={
        "success": True,
        "internal_code": "TRANSFER_SUCCESS",
        "message": "Transferencia realizada exitosamente",
        "data": response_body,
    })

    # Webhook en background (no bloquea la respuesta ni revierte la transacción)
    tenant = await tenant_repo.get_by_id(current_user.tenant_id)
    if tenant and tenant.webhook_url:
        background_tasks.add_task(
            webhook_dispatcher.dispatch,
            webhook_url=tenant.webhook_url,
            event_type="transfer.completed",
            payload=response_body,
            correlation_id=result.correlation_id,
        )

    return success_response(
        data=response_body,
        message="Transferencia realizada exitosamente",
        internal_code="TRANSFER_SUCCESS",
        status_code=status.HTTP_201_CREATED,
    )


# ---------------------------------------------------------------------------
# 5. Historial paginado con filtros
# ---------------------------------------------------------------------------

@router.get(
    "",
    summary="Historial paginado de transacciones",
)
async def get_transaction_history(
    request: Request,
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
    _attach_identity_to_state(request, current_user)

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
