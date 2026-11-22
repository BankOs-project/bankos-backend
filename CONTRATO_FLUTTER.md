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

## Perfil propio

Todas las rutas de esta sección usan `Authorization: Bearer <access_token>`. El servidor identifica al titular y al banco a partir de la sesión. Los perfiles de una misma persona en bancos distintos se editan por separado.

`GET /auth/me` · HTTP 200 · `USER_PROFILE` · `data`

```json
{
  "id": "aaaaaaaa-0002-0000-0000-000000000001",
  "tenant_id": "aaaaaaaa-0000-0000-0000-000000000001",
  "email": "juan@banco-andino.com",
  "full_name": "Juan Pérez Gómez",
  "role": "cliente",
  "is_active": true,
  "created_at": "2026-11-19T15:00:00+00:00",
  "email_verified_at": null,
  "document_type": null,
  "document_number": null,
  "phone": null,
  "address": null,
  "photo_url": null
}
```

Flutter puede seguir restaurando la sesión con los mismos campos, token y banco. `email_verified_at` es una fecha ISO 8601 con zona horaria o `null` si el correo aún no se ha verificado, como en algunos usuarios existentes. Trata el documento, la dirección y la foto como datos privados y evita escribirlos en los logs, incluso en los interceptores de Dio.

`PATCH /auth/me` recibe JSON y devuelve HTTP 200, `USER_PROFILE_UPDATED` y el mismo `data` que `GET /auth/me`, con los cambios aplicados. Omite los campos que quieras conservar. Para limpiar un campo opcional envía `null`. También se acepta una cadena vacía o de solo espacios para limpiar un campo opcional.

```json
{
  "full_name": "Juan Pérez Gómez",
  "document_type": "CC",
  "document_number": "0012345678",
  "phone": "+573001234567",
  "address": "Calle 10 # 20-30"
}
```

| Campo | Formato |
| --- | --- |
| `full_name` | Entre 3 y 150 caracteres imprimibles después de quitar espacios exteriores. Si se envía, es obligatorio. |
| `document_type` | `CC` para cédula de ciudadanía o `CE` para cédula de extranjería. |
| `document_number` | Texto de 3 a 12 dígitos ASCII. Conserva ceros iniciales. |
| `phone` | Formato internacional con `+` y de 8 a 15 dígitos. El primero debe ser distinto de cero. |
| `address` | Hasta 250 caracteres imprimibles después de quitar espacios exteriores. |

El tipo y el número deben quedar ambos presentes o ambos vacíos. El documento es un dato declarado. Editarlo conserva el UUID de persona, las vinculaciones y las cuentas. La acreditación de titularidad del documento queda pendiente de un proceso de verificación independiente. El `PATCH` rechaza campos ajenos a la tabla, incluido `email`.

### Foto privada

| Solicitud | Resultado |
| --- | --- |
| `PUT /auth/me/photo` con `Content-Type: image/jpeg` o `image/png` y los bytes como cuerpo | HTTP 200 · `PROFILE_PHOTO_UPDATED` · perfil completo. Sustituye la foto anterior. |
| `GET /auth/me/photo` | HTTP 200 · bytes con el `Content-Type` de la imagen. Si falta, HTTP 404 · `PROFILE_PHOTO_NOT_FOUND`. |
| `DELETE /auth/me/photo` | HTTP 200 · `PROFILE_PHOTO_REMOVED` · perfil con `photo_url: null`. Puede repetirse. |

Envía una imagen estática de hasta 2048 × 2048 píxeles y 2 MiB. Envía los bytes directamente, sin `multipart/form-data` ni base64. El servidor valida el formato real y guarda una imagen reconstruida a partir de sus píxeles, sin metadatos como EXIF o GPS. El límite de 2 MiB también se aplica a la imagen guardada. `photo_url` es la ruta privada `/api/v1/auth/me/photo` relativa al origen. Con Dio configurado con la base `/api/v1`, solicita `/auth/me/photo` con Bearer y `ResponseType.bytes`. Actualiza los bytes después de subir o quitar la foto. Las respuestas privadas de éxito incluyen `Cache-Control: private, no-store`.

### Cambiar el correo de acceso

1. Envía `POST /auth/me/email-change` con el correo nuevo y la contraseña actual de ese banco.

   ```json
   {"new_email":"juan.nuevo@example.com","current_password":"<contraseña actual>"}
   ```

   HTTP 202 · `PROFILE_EMAIL_PENDING` · `data`

   ```json
   {"request_id":"b2232afd-4d66-4416-8ebb-7a624415e92a","expires_in":1200}
   ```

2. Envía `POST /auth/me/email-change/verify` con el identificador de la solicitud y el código de un solo uso recibido en el correo nuevo. Usa el Bearer de la misma relación bancaria.

   ```json
   {"request_id":"b2232afd-4d66-4416-8ebb-7a624415e92a","email_code":"<código recibido>"}
   ```

   HTTP 200 · `PROFILE_EMAIL_UPDATED` · perfil completo con `email` y `email_verified_at` actualizados. Actualiza el correo en la sesión local. El JWT conserva su vigencia habitual. El correo anterior funciona para iniciar sesión hasta la confirmación. El nuevo correo se guarda en minúsculas, tiene un máximo de 255 caracteres y debe estar libre en el mismo banco.

La última solicitud aceptada reemplaza a la anterior. Si vence el código, inicia una solicitud nueva. El backend admite hasta tres solicitudes por usuario y por correo destino dentro del banco cada 15 minutos y hasta diez intentos de verificación por usuario en el mismo período. Usa `Retry-After` cuando recibas HTTP 429. Evita registrar la contraseña o el código.

| HTTP | `internal_code` | Manejo en Flutter |
| --- | --- | --- |
| 401 | `UNAUTHORIZED` / `AUTHENTICATION_FAILED` | Revisa la sesión. En la solicitud de correo, pide comprobar la contraseña si la sesión sigue vigente. |
| 400 | `PROFILE_EMAIL_INVALID` | El código o la solicitud no corresponden, vencieron o ya se usaron. |
| 409 | `PROFILE_EMAIL_UNCHANGED` / `PROFILE_EMAIL_UNAVAILABLE_IN_BANK` | Solicita otro correo para ese banco. |
| 413 | `PROFILE_PHOTO_TOO_LARGE` | Reduce el tamaño del archivo. |
| 415 | `PROFILE_PHOTO_UNSUPPORTED_TYPE` | Selecciona una foto JPEG o PNG. |
| 422 | `PROFILE_PHOTO_INVALID` / `PROFILE_DOCUMENT_INCOMPLETE` / `VALIDATION_ERROR` | Revisa la foto, el par de campos de documento o los campos indicados. |
| 429 | `RATE_LIMIT_EXCEEDED` | Espera lo indicado en `Retry-After`. |
| 503 | `PROFILE_EMAIL_UNAVAILABLE` | Conserva el correo mostrado y permite intentarlo más tarde. |

### Migración en Railway

El cambio agrega `user_profiles` y `profile_email_changes`. Los usuarios existentes pueden entrar sin fila de perfil. Sus identificadores, correos, contraseñas, vínculos y cuentas se conservan.

Si Railway usa `DATABASE_MANAGED_SCHEMA=true`, ejecuta antes del despliegue el comando de la nueva versión con `DATABASE_URL` del **dueño del esquema**. Sustituye `bankos_app` si la API usa otro rol. El comando crea las tablas y actualiza los permisos del rol de ejecución. Mantén en la API la URL de ese rol.

```sh
uv run --locked python -m scripts.provision_database bankos_app
```

En una imagen que ya tiene las dependencias instaladas, el comando equivalente es `python -m scripts.provision_database bankos_app`. Con `DATABASE_MANAGED_SCHEMA=false`, el arranque aplica la preparación de esquema con el rol que tiene DDL. Para el envío, configura `RESEND_API_KEY` y `REGISTRATION_EMAIL_FROM` con un remitente verificado en Resend, igual que para el registro. El cambio de correo también requiere el Redis configurado para limitar intentos.

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

`GET /accounts/1000000002` devuelve una sola cuenta bajo `data` con código `ACCOUNT_DETAIL_FETCHED`. Además del saldo, la moneda y el estado, el detalle y las respuestas de cambio de estado incluyen los campos de reactivación. El listado también los devuelve.

```json
{
  "account_number": "1000000002",
  "balance": "1500.0000",
  "currency": "USD",
  "status": "ACTIVE",
  "reactivated_at": "2026-11-20T14:00:00Z",
  "can_deactivate_at": "2026-11-21T14:00:00Z",
  "requires_admin_reactivation": false
}
```

El ejemplo abrevia `data`. `reactivated_at` registra la última reactivación y `can_deactivate_at` indica cuándo puede volver a desactivar el titular. Ambas fechas son instantes UTC y pueden ser `null` si nunca hubo una reactivación registrada. El vencimiento se fija al reactivar con la configuración del servidor, `ACCOUNT_DEACTIVATION_COOLDOWN_HOURS`, cuyo valor inicial es **24 horas**. Un cambio de configuración aplica a reactivaciones futuras. El titular puede desactivar en el instante exacto de vencimiento.

### Cambiar el estado de una cuenta

Ambas solicitudes usan Bearer y el número de cuenta en la URL. Envía la petición sin cuerpo.

| Solicitud | Éxito | Estado resultante |
| --- | --- | --- |
| `PATCH /accounts/{account_number}/deactivate` | 200 · `ACCOUNT_DEACTIVATED` | `INACTIVE` |
| `PATCH /accounts/{account_number}/reactivate` | 200 · `ACCOUNT_REACTIVATED` | `ACTIVE` |

Flutter permite operar en cuentas `ACTIVE`. Para `INACTIVE`, muestra la opción de reactivar si `requires_admin_reactivation` es `false`. Para `BLOCKED`, muestra el bloqueo administrativo y deshabilita ambos cambios de estado. Una desactivación administrativa conserva `INACTIVE` y exige que el banco la revierta. El administrador puede desactivar dentro de su banco incluso durante el plazo del titular. Repetir una solicitud que ya llegó al estado pedido devuelve el mismo estado y conserva el vencimiento anterior.

| HTTP y código | Qué muestra Flutter |
| --- | --- |
| 401 · `UNAUTHORIZED` | Pedir inicio de sesión. |
| 403 · `AUTHORIZATION_FAILED` | Indicar que esa cuenta no pertenece al usuario. |
| 403 · `ACCOUNT_REACTIVATION_REQUIRES_ADMIN` | Indicar que el banco debe reactivar la cuenta. |
| 403 · `ACCOUNT_BLOCKED` | Mostrar el bloqueo e indicar que el banco debe atenderlo. |
| 404 · `RESOURCE_NOT_FOUND` | Mostrar que la cuenta no está disponible en el banco de la sesión. |
| 409 · `ACCOUNT_DEACTIVATION_COOLDOWN` | Mostrar `data.can_deactivate_at` en la zona del usuario. El servidor decide cuándo venció el plazo. |
| 400 · `ACCOUNT_INACTIVE` | Al intentar una operación financiera, mostrar el estado inactivo y ofrecer la reactivación si corresponde. |

Ejemplo abreviado del error de plazo

```json
{
  "success": false,
  "internal_code": "ACCOUNT_DEACTIVATION_COOLDOWN",
  "message": "La cuenta todavía está en el plazo de espera después de su reactivación",
  "data": {"can_deactivate_at": "2026-11-21T14:00:00+00:00"}
}
```

Los cambios guardan el estado y la auditoría en la misma transacción. Conservan el saldo y el historial. Al aprovisionar una base existente, el historial de auditoría disponible identifica las cuentas inactivadas por un administrador. Las cuentas inactivas históricas sin evidencia administrativa quedan disponibles para reactivación por el titular.

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
  "fee_calculated": "0.0000",
  "amount_to_debit": "10.0000",
  "exchange_rate_applied": "4200.000000",
  "final_amount_to_receive": "42000.0000",
  "quote_id": "ffe871b8-9d29-47d0-9ea9-198dfa83bfe7",
  "expires_at": "2026-11-20T15:17:00Z",
  "transfer_mode": "SAME_BANK",
  "recipient": null
}
```

Muestra el desglose tal como lo devuelve el backend. En «Mismo banco» la cuenta origen se debita por 10 USD y la cuenta destino recibe 42.000 COP. La comisión es cero. Para cotizar `DEPOSIT` o `WITHDRAWAL`, envía `operation_type`, `source_account_number` con la cuenta propia y `amount`. La cotización del depósito tiene débito cero y calcula el abono. La del retiro muestra el cargo y el monto a retirar. La cotización no reserva fondos.

### Transferencia simulada entre bancos

Flutter toma el identificador público del banco de `GET /banks` y envía el número como texto. El destinatario puede recibir sin iniciar un trámite. El servidor consulta el número únicamente dentro del banco indicado. El entorno debe tener `SIMULATED_INTERBANK_ENABLED=true` y el esquema aprovisionado.

```http
POST /api/v1/transactions/quote
Authorization: Bearer <token>
Content-Type: application/json
```

```json
{
  "operation_type": "TRANSFER",
  "transfer_mode": "OTHER_BANK",
  "source_account_number": "3200000002",
  "destination_bank_id": "0d54d975-88a4-49ed-9e0e-af19ed20af70",
  "destination_account_number": "3300000003",
  "amount": "10.00"
}
```

HTTP 200 · `QUOTE_CALCULATED` · `data`. Usa el identificador real del directorio para seleccionar el banco.

```json
{
  "operation_type": "TRANSFER",
  "transfer_mode": "OTHER_BANK",
  "quote_id": "ffe871b8-9d29-47d0-9ea9-198dfa83bfe7",
  "expires_at": "2026-11-20T15:17:00Z",
  "source_currency": "USD",
  "destination_currency": "COP",
  "original_amount": "10.00",
  "fee_calculated": "0.1000",
  "amount_to_debit": "10.1000",
  "exchange_rate_applied": "4100.000000",
  "final_amount_to_receive": "41000.0000",
  "recipient": {
    "bank_name": "DaviPlata",
    "masked_holder_name": "N*** P*** D***",
    "account_last_four": "0003"
  }
}
```

En «Otro banco», la comisión y la tasa salen de la configuración de origen. Muestra un aviso de simulación y solo los campos de `recipient` para identificar el destino. Para «Mismo banco», Flutter puede seguir usando la consulta local `/accounts/{number}/destination`. La cotización de «Mismo banco» admite `transfer_mode: "SAME_BANK"` u omitir ese campo.

Para ambas modalidades, confirma con el `quote_id` sin volver a enviar monto ni destino. La cotización vence a los cinco minutos, queda ligada al usuario que la pidió y solo permite una confirmación exitosa. Ante un fallo de red, reenvía el mismo cuerpo con las mismas claves.

```http
POST /api/v1/transactions/transfer
Authorization: Bearer <token>
Content-Type: application/json
Idempotency-Key: 34ac2258-ecef-4e61-bc96-a8870088caf2
X-Correlation-ID: 9a17c200-0e47-4b8e-9d18-4db1bd057402
```

```json
{"quote_id":"ffe871b8-9d29-47d0-9ea9-198dfa83bfe7"}
```

HTTP 201 · `TRANSFER_SUCCESS` · campos abreviados del comprobante del remitente

```json
{
  "transfer_mode": "OTHER_BANK",
  "transfer_reference": "8acc51ea-4aea-4100-9e15-397af6b496da",
  "source_account_number": "3200000002",
  "destination_account_number": null,
  "original_amount": "10.0000",
  "original_currency": "USD",
  "converted_amount": "41000.0000",
  "destination_currency": "COP",
  "fee_charged": "0.1000",
  "status": "SUCCESS"
}
```

En el historial del receptor aparece la misma referencia, solo la cuenta destino y el abono local. En esa fila, `source_account_number` es `null`, ambos importes representan los COP acreditados y la comisión local es cero. Los identificadores de la cuenta, usuario y tenant remotos quedan fuera del comprobante. La referencia común no sirve para consultar el otro lado. Cada JWT solo permite consultar su propio tenant.

| HTTP · código | Qué hace Flutter |
| --- | --- |
| 404 · `DESTINATION_UNAVAILABLE` | Muestra «El destino no está disponible» y vuelve al formulario. El mismo error cubre bancos o cuentas inexistentes o no habilitados para recibir. |
| 404 · `QUOTE_UNAVAILABLE` | Solicita una nueva cotización. |
| 409 · `QUOTE_EXPIRED`, `QUOTE_STALE` | Vuelve a cotizar y muestra el nuevo desglose para aprobación. |
| 409 · `QUOTE_ALREADY_USED` | Consulta el historial. Si fue un reintento por desconexión, reutiliza la clave original. |
| 403 · `INTERBANK_DISABLED` | Muestra que la modalidad simulada está deshabilitada en el entorno. |
| 429 · `RATE_LIMIT_EXCEEDED` | Respeta `Retry-After`. La consulta entre bancos permite ocho intentos por minuto por usuario, además de los límites generales. |
| 503 · `TRANSFER_QUOTE_UNAVAILABLE` | Muestra indisponibilidad temporal de la cotización. |

El formato anterior de ejecución con cuenta de origen, número destino y monto se conserva para «Mismo banco», con comisión cero. Flutter debe usar `quote_id` para confirmar «Otro banco» y para fijar las condiciones en cualquiera de las dos modalidades.

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
    "fee_charged": "0.0000",
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

## Resumen financiero del dashboard

`GET /transactions/summary` usa Bearer y requiere `start_date` y `end_date`. Omite `account_number` para consultar todas las cuentas propias. Envía el número como texto para consultar una sola cuenta propia. Una cuenta ajena o inexistente devuelve `403 AUTHORIZATION_FAILED`.

Las dos fechas representan instantes ISO 8601 con zona explícita, como `Z` o `-05:00`. Ambos extremos son inclusivos y la respuesta los devuelve en UTC. El inicio y el fin pueden ser iguales para consultar un instante exacto. Un rango invertido devuelve `422 INVALID_DATE_RANGE`. Una fecha sin hora o sin zona devuelve `422 VALIDATION_ERROR`. Para un período de calendario en Bogotá, convierte el inicio y el último instante del período a UTC antes de enviarlos.

- `available_balance` es el saldo disponible al momento de la consulta. Suma el saldo de las cuentas activas de la selección. Una cuenta inactiva o bloqueada aporta cero de disponibilidad, y sus movimientos confirmados anteriores siguen contando.
- `income` suma depósitos y transferencias recibidas en la moneda y el importe acreditados. `expenses` suma retiros y transferencias enviadas en la moneda de origen, incluida la comisión.
- Si ambos lados de una transferencia pertenecen a las cuentas seleccionadas, solo cuenta la comisión como gasto en la moneda de origen, incluso si hubo conversión. Al seleccionar una sola cuenta, cuenta el lado de esa cuenta. Para un período se consideran únicamente movimientos confirmados. Un saldo inicial sin un movimiento registrado solo contribuye a disponibilidad.
- Los importes se devuelven como texto decimal. Flutter debe mantener cada divisa por separado. Si hay cuentas pero no hubo movimientos en el rango, los flujos son cero. Un usuario sin cuentas recibe `currencies: []`.

Ejemplo para todas las cuentas, con el inicio a medianoche en Bogotá

```http
GET /transactions/summary?start_date=2026-11-01T00:00:00-05:00&end_date=2026-11-03T05:00:00Z
```

HTTP 200 · `FINANCIAL_SUMMARY_FETCHED`

```json
{
  "success": true,
  "internal_code": "FINANCIAL_SUMMARY_FETCHED",
  "message": "Resumen financiero obtenido",
  "data": {
    "account_number": null,
    "start_date": "2026-11-01T05:00:00Z",
    "end_date": "2026-11-03T05:00:00Z",
    "currencies": [
      {"currency": "COP", "available_balance": "425000.0000", "income": "225.0000", "expenses": "121.0000"},
      {"currency": "USD", "available_balance": "80.0000", "income": "73.0000", "expenses": "0.1500"}
    ]
  }
}
```

Con las mismas fechas y `account_number=3200000001`, el abono de la transferencia propia USD a COP sí aparece como ingreso de esa cuenta. La respuesta trae este `data`.

```json
{
  "account_number": "3200000001",
  "start_date": "2026-11-01T05:00:00Z",
  "end_date": "2026-11-03T05:00:00Z",
  "currencies": [
    {"currency": "COP", "available_balance": "425000.0000", "income": "41225.0000", "expenses": "121.0000"}
  ]
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
