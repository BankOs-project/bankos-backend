"""Servicio de dominio para cálculos financieros con precisión Decimal."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from app.core.exceptions import BankOSException
from app.modules.tenants.domain.entities import FeeType, Tenant


QUANTIZE_4 = Decimal("0.0001")
QUANTIZE_6 = Decimal("0.000001")
ONE = Decimal("1")


def calculate_fee(amount: Decimal, tenant: Tenant) -> Decimal:
    """Calcula la comisión aplicable a una transacción saliente según la configuración del tenant."""
    if tenant.fee_type == FeeType.FIXED:
        return tenant.fee_value.quantize(QUANTIZE_4, rounding=ROUND_HALF_UP)
    # PERCENTAGE: aplica el porcentaje sobre el monto original
    fee = (amount * tenant.fee_value / Decimal("100")).quantize(QUANTIZE_4, rounding=ROUND_HALF_UP)
    return fee


def get_exchange_rate(source_currency: str, dest_currency: str, tenant: Tenant) -> Decimal:
    """
    Obtiene la tasa de cambio estática del tenant entre dos divisas.

    La tabla `exchange_rates` del tenant debe estar en el formato:
      { "USD_COP": "4200.0000", "EUR_COP": "4500.0000", "USD_EUR": "0.9200", ... }

    Si las divisas son iguales, la tasa es 1.
    """
    src = source_currency.upper()
    dst = dest_currency.upper()

    if src == dst:
        return ONE

    key = f"{src}_{dst}"
    reverse_key = f"{dst}_{src}"

    rates = tenant.exchange_rates

    for rate_key, invert in ((key, False), (reverse_key, True)):
        if rate_key not in rates:
            continue
        try:
            rate = Decimal(str(rates[rate_key]))
            if not rate.is_finite() or rate <= 0:
                continue
            resolved_rate = (ONE / rate if invert else rate).quantize(
                QUANTIZE_6, rounding=ROUND_HALF_UP,
            )
            if resolved_rate > 0:
                return resolved_rate
        except InvalidOperation:
            continue

    raise BankOSException(
        message=f"No hay una tasa de cambio disponible de {src} a {dst}",
        internal_code="EXCHANGE_RATE_UNAVAILABLE",
        status_code=422,
    )


def apply_conversion(amount: Decimal, rate: Decimal) -> Decimal:
    """Aplica la tasa de cambio a un monto y retorna el resultado con precisión de 4 decimales."""
    converted = (amount * rate).quantize(QUANTIZE_4, rounding=ROUND_HALF_UP)
    if converted <= 0:
        raise BankOSException(
            message="El monto convertido debe ser mayor que cero",
            internal_code="INVALID_AMOUNT",
            status_code=422,
        )
    return converted

