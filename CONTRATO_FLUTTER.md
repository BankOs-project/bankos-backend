# Contrato de BankOS para Flutter

La base de la API termina en `/api/v1`. En Dio, configura esa base una sola vez y usa las rutas de abajo de forma relativa a ella. Esta guía describe los cambios locales sobre `dev` (`69bf308`).

## Encabezados y respuesta común

```http
Authorization: Bearer <access_token>
Content-Type: application/json
X-Correlation-ID: 9a17c200-0e47-4b8e-9d18-4db1bd057402
Idempotency-Key: 34ac2258-ecef-4e61-bc96-a8870088caf2
```

- Envía `Authorization` después del login y `Content-Type` en solicitudes con JSON.
- Genera un `X-Correlation-ID` por solicitud. El servidor lo devuelve en el encabezado de respuesta y lo genera si falta.
- Envía `Idempotency-Key` únicamente en depósito, retiro y transferencia. Genera un UUID v4 cuando el usuario confirme un movimiento. Ante un timeout, un error de red o un `5xx`, conserva el cuerpo, la ruta y ambos identificadores. El campo legado `idempotency_key` del JSON es opcional y, si lo envías, debe coincidir con el encabezado.
- Lee `success`, `internal_code`, `message` y `data`. Los ejemplos con la etiqueta `data` muestran solo ese campo. Trata los números de cuenta como texto. Envía montos positivos como texto decimal con punto y hasta cuatro decimales, por ejemplo `"10.00"`. Lee los montos y tasas devueltos como texto decimal para conservar la precisión.

## Elige tu banco y login

`GET /banks` es público y no requiere Bearer. Flutter lee `data`, muestra `name` y usa un icono local si `logo_url` es `null`. La API devuelve `null` si falta un logo HTTPS válido. Guarda `bank_id` como texto opaco y úsalo en el login. Si no hay bancos habilitados, `data` es `[]`. El directorio contiene únicamente bancos activos que se habilitaron expresamente para aparecer.

HTTP 200 · `BANKS_FETCHED`

```json
{
  "success": true,
  "internal_code": "BANKS_FETCHED",
  "message": "Directorio de bancos obtenido",
  "data": [
    {
      "bank_id": "0d54d975-88a4-49ed-9e0e-af19ed20af70",
      "name": "Banco Andino S.A.",
      "logo_url": null
    }
  ]
}
```

`POST /auth/login` es público. Envía el `bank_id` que realmente devolvió el directorio junto con el correo y la contraseña. El identificador del ejemplo es ilustrativo.

```json
{"bank_id":"0d54d975-88a4-49ed-9e0e-af19ed20af70","email":"juan@banco-andino.com","password":"<contraseña-demo>"}
```

Durante la transición, la app anterior puede seguir enviando `tenant_slug`. Cada solicitud debe llevar un solo selector.

```json
{"tenant_slug":"banco-andino","email":"juan@banco-andino.com","password":"<contraseña-demo>"}
```

Un banco desconocido, un selector público que no está disponible o unas credenciales inválidas devuelven el mismo error.

HTTP 401 · `AUTHENTICATION_FAILED`

```json
{
  "success": false,
  "internal_code": "AUTHENTICATION_FAILED",
  "message": "Institución bancaria o credenciales no válidas",
  "data": null
}
```

HTTP 200 · `LOGIN_SUCCESS` · `data`

```json
{
  "access_token": "<JWT>",
  "token_type": "bearer",
  "expires_in": 3600,
  "user_id": "aaaaaaaa-0002-0000-0000-000000000001",
  "tenant_id": "aaaaaaaa-0000-0000-0000-000000000001",
  "role": "cliente"
}
```

`GET /auth/me` con Bearer · HTTP 200 · `USER_PROFILE` · `data`

```json
{
  "id": "aaaaaaaa-0002-0000-0000-000000000001",
  "tenant_id": "aaaaaaaa-0000-0000-0000-000000000001",
  "email": "juan@banco-andino.com",
  "full_name": "Juan Pérez Gómez",
  "role": "cliente",
  "is_active": true,
  "created_at": "2026-11-19T15:00:00+00:00"
}
```

Flutter guarda el token y el banco de la sesión. Usa `full_name` para el saludo. El backend extrae el tenant de las peticiones autenticadas desde el JWT.

## Qué significa el ID personal

BankOS usa un UUID interno de persona para vincular deliberadamente usuarios de distintos bancos. Cada relación bancaria tiene su propio `user_id`, correo, contraseña y rol. El UUID de persona representa una identidad de acceso dentro de BankOS. La identidad civil, la validación documental y el nivel de verificación requerido para KYC quedan por definir.

En el primer registro, la persona acredita control del correo del banco elegido. Para vincular un banco adicional, inicia el flujo con una sesión de su usuario anterior, vuelve a demostrar conocimiento de la contraseña de ese usuario y verifica el correo anterior y el del nuevo registro con pruebas de un solo uso. Estas pruebas acreditan control de las dos relaciones de acceso. La titularidad de un documento de identidad requeriría una verificación independiente, con evidencia y un proveedor o proceso aprobado.

Cada usuario existente recibe un UUID de persona propio mediante la migración. Conserva sus UUID de usuario y banco, sus cuentas, su rol y su acceso actual. La migración deja el correo como no verificado y conserva separadas las relaciones previas aunque coincidan los nombres o correos. La prueba de correo anterior durante una vinculación futura puede acreditar ese correo. El UUID de persona y las pruebas de vinculación se mantienen en el backend.

Queda por definir cómo conciliar dos perfiles existentes y cómo recuperar la vinculación cuando se pierde acceso al correo anterior.

## Registro y verificación de correo

Para un primer registro, envía `POST /auth/register` sin Bearer con el banco elegido y el correo que se usará allí.

```json
{"bank_id":"0d54d975-88a4-49ed-9e0e-af19ed20af70","email":"cliente@example.com"}
```

Para vincular otro banco, usa el mismo endpoint con `Authorization: Bearer <JWT_DEL_BANCO_ACTUAL>` y agrega `source_password` con la contraseña del banco actual. El `bank_id` y el `email` corresponden al nuevo banco. Guarda `registration_id` hasta terminar el flujo.

HTTP 202 · `REGISTRATION_PENDING` · `data`

```json
{"registration_id":"41b29661-f0c7-40b9-90f8-8ee1f0cb4d41","expires_in":1200}
```

Después de recibir el correo, envía `POST /auth/register/verify`. El nombre y la contraseña se asignan al usuario del banco elegido. En una vinculación, envía también `source_email_code` con el código del correo del banco anterior y conserva el Bearer de ese mismo usuario en la solicitud.

```json
{
  "registration_id": "41b29661-f0c7-40b9-90f8-8ee1f0cb4d41",
  "email_code": "<codigo-del-correo-del-nuevo-registro>",
  "full_name": "Ana Torres",
  "password": "<contraseña-nueva-de-al-menos-8-caracteres>"
}
```

HTTP 201 · `REGISTRATION_VERIFIED` · `data: null`. Flutter puede iniciar sesión con el banco, el correo y la contraseña nuevos. El alta pública asigna el rol `cliente`. La apertura de una cuenta bancaria sigue en su flujo independiente.

Los códigos son de un solo uso, distinguen mayúsculas y minúsculas y vencen en 20 minutos. La confirmación inválida, vencida, usada o duplicada en ese banco devuelve `400 REGISTRATION_INVALID`. Un fallo al autenticar el usuario de origen devuelve `401`. Ante `429`, espera `Retry-After`. Si aparece `503 REGISTRATION_UNAVAILABLE`, vuelve a iniciar la solicitud cuando se restablezca el envío. Si se perdió un `201`, prueba primero el login con las credenciales nuevas.

El registro comprueba el correo dentro del banco elegido. La API no informa si ese correo o esa persona tiene una relación en otro banco.

## Cuentas propias y destino

`GET /accounts?skip=0&limit=50` con Bearer · HTTP 200 · `ACCOUNTS_FETCHED` · `data` abreviado

```json
{
  "total": 3,
  "skip": 0,
  "limit": 50,
  "items": [
    {
      "id": "aaaaaaaa-ac02-0000-0000-000000000001",
      "account_number": "1000000002",
      "owner_id": "aaaaaaaa-0002-0000-0000-000000000001",
      "tenant_id": "aaaaaaaa-0000-0000-0000-000000000001",
      "balance": "1500.0000",
      "currency": "USD",
      "status": "ACTIVE",
      "created_at": "2026-11-19T15:00:00Z",
      "updated_at": "2026-11-19T15:00:00Z"
    }
  ]
}
```

`GET /accounts/1000000002` devuelve una sola cuenta con esos campos bajo `data` y código `ACCOUNT_DETAIL_FETCHED`. Muestra saldo, moneda y estado. Habilita operaciones en cuentas `ACTIVE`. Los otros estados son `INACTIVE` y `BLOCKED`.

`GET /accounts/1000000003/destination` con Bearer · HTTP 200 · `TRANSFER_DESTINATION_FOUND`

```json
{
  "success": true,
  "internal_code": "TRANSFER_DESTINATION_FOUND",
  "message": "Cuenta destino encontrada",
  "data": {
    "account_number": "1000000003",
    "holder_name": "Jaysir Martínez Ruiz",
    "currency": "COP"
  }
}
```

Usa esta consulta cuando el usuario termine de escribir el número e indique que quiere buscarlo. Muestra el nombre, el número y la moneda para confirmar. Una cuenta inexistente, inactiva, bloqueada o de otro banco devuelve el mismo 404. Si el destino es una cuenta del propio cliente, también puedes usar el número que ya obtuviste en el listado.

## Cotización y envío

`POST /transactions/quote` con Bearer · HTTP 200 · `QUOTE_CALCULATED`

```json
{
  "operation_type": "TRANSFER",
  "source_account_number": "1000000002",
  "destination_account_number": "1000000003",
  "amount": "10.00"
}
```

`data`

```json
{
  "operation_type": "TRANSFER",
  "source_currency": "USD",
  "destination_currency": "COP",
  "original_amount": "10.00",
  "fee_calculated": "0.1500",
  "amount_to_debit": "10.1500",
  "exchange_rate_applied": "4200.000000",
  "final_amount_to_receive": "42000.0000"
}
```

Muestra el desglose tal como lo devuelve el backend. En este ejemplo, la cuenta origen se debita por 10,15 USD y la cuenta destino recibe 42.000 COP. Para cotizar `DEPOSIT` o `WITHDRAWAL`, envía `operation_type`, `source_account_number` con la cuenta propia y `amount`. La cotización del depósito tiene débito cero y calcula el abono. La del retiro muestra el cargo y el monto a retirar. La cotización no reserva fondos. La confirmación vuelve a aplicar las reglas vigentes.

Las tres solicitudes siguientes usan Bearer y su propia `Idempotency-Key`. En Flutter, confirma el retiro o la transferencia con el PIN o la biometría local antes de enviar.

| Solicitud | JSON | HTTP y código |
| --- | --- | --- |
| `POST /transactions/deposit` | `{"account_number":"1000000002","amount":"20.00"}` | 201 · `DEPOSIT_SUCCESS` |
| `POST /transactions/withdraw` | `{"account_number":"1000000002","amount":"5.00"}` | 201 · `WITHDRAWAL_SUCCESS` |
| `POST /transactions/transfer` | `{"source_account_number":"1000000002","destination_account_number":"1000000003","amount":"10.00"}` | 201 · `TRANSFER_SUCCESS` |

En la transferencia envía el monto original en la moneda de origen. Usa la respuesta de confirmación para el comprobante y vuelve a consultar las cuentas para actualizar saldos. Los identificadores y fechas de los ejemplos son ilustrativos.

Respuesta de transferencia

```json
{
  "success": true,
  "internal_code": "TRANSFER_SUCCESS",
  "message": "Transferencia realizada exitosamente",
  "data": {
    "id": "8acc51ea-4aea-4100-9e15-397af6b496da",
    "tenant_id": "aaaaaaaa-0000-0000-0000-000000000001",
    "operation_type": "TRANSFER",
    "source_account_number": "1000000002",
    "destination_account_number": "1000000003",
    "original_amount": "10.0000",
    "original_currency": "USD",
    "converted_amount": "42000.0000",
    "destination_currency": "COP",
    "exchange_rate_applied": "4200.000000",
    "fee_charged": "0.1500",
    "status": "SUCCESS",
    "idempotency_key": "34ac2258-ecef-4e61-bc96-a8870088caf2",
    "correlation_id": "9a17c200-0e47-4b8e-9d18-4db1bd057402",
    "created_at": "2026-11-19T15:12:00Z"
  }
}
```

Depósito y retiro usan el mismo esquema de transacción. Estos son los campos que distinguen ambos ejemplos.

```json
{
  "operation_type": "DEPOSIT",
  "source_account_number": null,
  "destination_account_number": "1000000002",
  "original_amount": "20.0000",
  "original_currency": "USD",
  "converted_amount": "20.0000",
  "destination_currency": "USD",
  "exchange_rate_applied": "1.000000",
  "fee_charged": "0.0000",
  "status": "SUCCESS"
}
```

```json
{
  "operation_type": "WITHDRAWAL",
  "source_account_number": "1000000002",
  "destination_account_number": null,
  "original_amount": "5.0000",
  "original_currency": "USD",
  "converted_amount": "5.0000",
  "destination_currency": "USD",
  "exchange_rate_applied": "1.000000",
  "fee_charged": "0.0000",
  "status": "SUCCESS"
}
```

## Historial y paginación

```http
GET /transactions?account_number=1000000002&operation_type=TRANSFER&start_date=2026-11-01T00:00:00Z&end_date=2026-11-30T23:59:59.999999Z&skip=0&limit=1
```

HTTP 200 · `TRANSACTIONS_FETCHED` · `data` abreviado

```json
{
  "total": 2,
  "skip": 0,
  "limit": 1,
  "items": [
    {
      "id": "8acc51ea-4aea-4100-9e15-397af6b496da",
      "operation_type": "TRANSFER",
      "source_account_number": "1000000002",
      "destination_account_number": "1000000003",
      "original_amount": "10.0000",
      "original_currency": "USD",
      "fee_charged": "0.1500",
      "converted_amount": "42000.0000",
      "destination_currency": "COP",
      "created_at": "2026-11-19T15:12:00Z"
    }
  ]
}
```

- Cada elemento usa el esquema completo del comprobante. El cliente ve movimientos en los que participa alguna cuenta propia, incluidas transferencias entrantes.
- Los filtros son opcionales y se pueden combinar. Los tipos válidos son `DEPOSIT`, `WITHDRAWAL` y `TRANSFER`. Usa instantes ISO 8601 con zona horaria para los filtros. Los extremos son inclusivos. Para un día del calendario, convierte a UTC el inicio y el fin del día en la zona que muestre Flutter.
- Historial y cuentas usan `skip` desde 0 y `limit` de 1 a 100, con valor predeterminado de 50. Para pedir la siguiente página, conserva los filtros y usa `skip + items.length` mientras el resultado sea menor que `total`. Si cambia un filtro, vuelve a `skip=0`.
- Usa `fee_charged` para marcar operaciones con comisión y compara `original_currency` con `destination_currency` para marcar conversiones.
- Muestra los registros con `status: "ERROR"` como intentos rechazados. `failure_code` indica el motivo y los importes de cobro y abono son cero. `XXX` indica que la moneda no estaba disponible para ese intento. El cliente ve sus propios rechazos y los administradores ven los de su tenant. Los errores de autenticación, formato, rate limiting e infraestructura no generan un movimiento fallido en el historial.

## Errores que Flutter debe manejar

Ejemplo para un destino que el usuario no puede utilizar, HTTP 404

```json
{
  "success": false,
  "internal_code": "RESOURCE_NOT_FOUND",
  "message": "Cuenta destino no disponible en esta institución",
  "data": null
}
```

| HTTP | `internal_code` | Manejo en Flutter |
| --- | --- | --- |
| 401 | `UNAUTHORIZED`, `AUTHENTICATION_FAILED` | Pedir login si expiró el token o mostrar el error de credenciales en el formulario. |
| 403 | `AUTHORIZATION_FAILED`, `ACCOUNT_BLOCKED` | Mostrar el mensaje y actualizar las cuentas si cambió el estado. |
| 404 | `RESOURCE_NOT_FOUND` | Pedir que se revise el número de cuenta. |
| 400 | `ACCOUNT_INACTIVE`, `MISSING_IDEMPOTENCY_KEY` | Actualizar la cuenta inactiva o corregir el encabezado que envía la app. |
| 409 | `IDEMPOTENCY_IN_PROGRESS` | Esperar y reintentar con los mismos identificadores. |
| 409 | `IDEMPOTENCY_KEY_REUSED`, `LEGACY_IDEMPOTENCY_KEY` | Revisar el historial antes de intentar confirmar otro movimiento. |
| 422 | `VALIDATION_ERROR`, `INVALID_DATE_RANGE`, `INVALID_IDEMPOTENCY_KEY`, `IDEMPOTENCY_KEY_MISMATCH` | Corregir los datos. Para `VALIDATION_ERROR`, `data.validation_errors` incluye `field`, `message` y `type`. |
| 422 | `INSUFFICIENT_FUNDS`, `TRANSACTION_LIMIT_EXCEEDED`, `EXCHANGE_RATE_UNAVAILABLE`, `INVALID_AMOUNT` | Mostrar el mensaje y volver a cotizar si se corrige la operación. |
| 429 | `RATE_LIMIT_EXCEEDED` | Esperar los segundos del encabezado `Retry-After`. |

PostgreSQL confirma los saldos, la transacción y el comprobante HTTP en el mismo commit. Después del commit, Redis guarda una copia hasta el mismo vencimiento. Durante 24 horas, un reintento del mismo usuario en el mismo tenant recupera el código HTTP y el cuerpo originales incluso si Redis perdió la copia. El `X-Correlation-ID` de respuesta refleja la solicitud que se acaba de recibir. El `data.correlation_id` del comprobante conserva el valor del movimiento original.

Si recibes `409 IDEMPOTENCY_IN_PROGRESS`, espera y vuelve a usar los mismos identificadores. Usa una clave nueva solo para un movimiento distinto. Si pasan 24 horas sin poder confirmar el resultado, revisa el historial antes de volver a enviar. Un registro anterior a los comprobantes conserva su clave en el historial. Dentro de la ventana, si ese registro carece de actor, la API devuelve `409 LEGACY_IDEMPOTENCY_KEY`.

## Verificación del backend

En ventanas de 60 segundos, el login permite 10 solicitudes por IP y 5 por cuenta de login (tenant y correo). Las rutas de transacciones comparten 30 solicitudes por IP y 60 por usuario autenticado dentro de su tenant. Cada `429` incluye `Retry-After`. Si Redis no responde, la API registra la incidencia y deja pasar la solicitud.

Con PostgreSQL y Redis desechables, prepara `bankos_app` con el script de aprovisionamiento del README. Configura `BANKOS_TEST_DATABASE_URL` con ese rol, `BANKOS_TEST_REDIS_URL` y `DATABASE_MANAGED_SCHEMA=true` para ejecutar toda la suite desde el backend.

```powershell
uv run --locked python -m unittest discover -s tests -v
```
