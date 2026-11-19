# BankOS — Backend (Monolito Modular + Clean Architecture)

Backend de **BankOS** (Sistema Bancario Multitenant como Servicio), desarrollado con **Python 3.12**, **FastAPI**, **SQLAlchemy 2.0 (Async)**, **PostgreSQL 15**, **Redis 7** y administrado con **uv**.

---

## 📌 Tabla de Contenidos
1. [Arquitectura del Sistema](#-arquitectura-del-sistema)
2. [Estado de la Conexión Frontend ⇄ Backend](#-estado-de-la-conexión-frontend--backend)
3. [Formas de Levantar el Backend](#-formas-de-levantar-el-backend)
   - [Opción A: Docker Compose (Recomendada)](#opción-a-docker-compose-recomendada)
   - [Opción B: Desarrollo Local Nativo con `uv`](#opción-b-desarrollo-local-nativo-con-uv)
4. [Poblar la Base de Datos (Seed de Demostración)](#-poblar-la-base-de-datos-seed-de-demostración)
5. [Credenciales para Pruebas](#-credenciales-para-pruebas)
6. [Configuración de Conexión en Flutter (Frontend)](#-configuración-de-conexión-en-flutter-frontend)
7. [Endpoints y Documentación Interactiva](#-endpoints-y-documentación-interactiva)
8. [Solución de Errores Frecuentes (Troubleshooting)](#-solución-de-errores-frecuentes-troubleshooting)

---

## 🏗️ Arquitectura del Sistema

El proyecto sigue una arquitectura de **Monolito Modular** donde cada módulo de negocio encapsula las 4 capas de **Clean Architecture**:

- `domain/`: Entidades de negocio, contratos de repositorio y servicios de dominio.
- `application/`: Casos de uso, DTOs y orquestación.
- `infrastructure/`: Modelos ORM, implementaciones de repositorios y adaptadores externos.
- `presentation/`: Controladores y routers de FastAPI (`/api/v1/*`).

El paquete `app/core/` provee infraestructura transversal común:
- `config.py`: Gestión tipada de variables de entorno con `pydantic-settings`.
- `database.py`: Motor asíncrono SQLAlchemy (`asyncpg`) y generador de `AsyncSession`.
- `redis.py`: Cliente asíncrono de Redis (`redis.asyncio`) para idempotencia y límites de tasa.
- `security.py`: Generación/validación de JWT con claims obligatorios (`tenant_id`, `user_id`, `role`) y hashing seguro de contraseñas con bcrypt.
- `responses.py` & `exceptions.py`: Envelope único estandarizado (`success`, `internal_code`, `message`, `data`) y manejadores globales de error.
- `middlewares/correlation_id.py`: Generación, propagación en `ContextVar` e inyección del encabezado `X-Correlation-ID` y filtrado para logging.

---

## 📡 Estado de la Conexión Frontend ⇄ Backend

La conectividad y el contrato de API están estandarizados para integrarse con clientes móviles y web:
- **API Base URL:** `http://localhost:8000/api/v1`
- **Health Check:** `GET /api/v1/health` ➔ `200 OK` (Base de datos y Redis conectados).
- **Autenticación:** `POST /api/v1/auth/login` ➔ `200 OK` con emisión de JWT.
- **Frontend Flutter:** El cliente móvil en `bankos-mobile` cuenta con `dio_provider.dart` configurado para resolver dinámicamente `localhost:8000` en Desktop/Web y `10.0.2.2:8000` en emuladores de Android.

---

## 🚀 Formas de Levantar el Backend

El backend requiere **Python 3.12+**, una base de datos **PostgreSQL 15** y una instancia de **Redis 7**.

### Opción A: Docker Compose (Recomendada)
Esta opción levanta automáticamente la API, PostgreSQL y Redis en contenedores aislados con todas sus dependencias.

1. **Abrir terminal en la carpeta del backend:**
   ```powershell
   cd C:\Users\Coder\Documents\bankos-backend
   ```

2. **Levantar los servicios:**
   ```powershell
   docker compose up --build -d
   ```

3. **Verificar que los contenedores estén corriendo:**
   ```powershell
   docker ps
   ```
   Deberías ver `bankos_api` (puerto 8000), `bankos_postgres` (puerto 5432) y `bankos_redis` (puerto 6379).

4. **Ver los logs en tiempo real:**
   ```powershell
   docker logs -f bankos_api
   ```

---

### Opción B: Desarrollo Local Nativo con `uv`
Si deseas ejecutar FastAPI directamente en tu máquina Windows para depurar con recarga rápida:

1. **Levantar solo la base de datos y Redis con Docker:**
   ```powershell
   cd C:\Users\Coder\Documents\bankos-backend
   docker compose up db redis -d
   ```
   *(Esto mantiene activos Postgres en el puerto 5432 y Redis en el 6379 sin ocupar el puerto 8000).*

2. **Crear el archivo `.env` para entorno local:**
   Copia el archivo `.env.example` como `.env` y asegúrate de que apunte a `localhost`:
   ```ini
   PROJECT_NAME="BankOS API"
   ENVIRONMENT="development"
   DEBUG=true
   SECRET_KEY="bankos-development-secret-key-change-this-in-production-min-32-chars-long!"
   JWT_ALGORITHM="HS256"
   ACCESS_TOKEN_EXPIRE_MINUTES=60

   # Conexión local a PostgreSQL
   POSTGRES_SERVER="localhost"
   POSTGRES_PORT=5432
   POSTGRES_USER="postgres"
   POSTGRES_PASSWORD="postgres_secure_password"
   POSTGRES_DB="bankos_db"
   DATABASE_URL="postgresql+asyncpg://postgres:postgres_secure_password@localhost:5432/bankos_db"

   # Conexión local a Redis
   REDIS_HOST="localhost"
   REDIS_PORT=6379
   REDIS_URL="redis://localhost:6379/0"

   CORS_ORIGINS=["*"]
   ```

3. **Sincronizar dependencias de Python con `uv`:**
   ```powershell
   uv sync
   ```

4. **Iniciar el servidor Uvicorn:**
   ```powershell
   uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

---

## 🗄️ Poblar la Base de Datos (Seed de Demostración)

La base de datos arranca inicialmente vacía. Para que el login funcione y existan cuentas para operar, se debe ejecutar el script `seed_demo.py`.

El script es **idempotente**: si los datos ya fueron cargados, no fallará ni creará duplicados.

- **Si estás usando Docker Compose:**
  ```powershell
  docker exec bankos_api python scripts/seed_demo.py
  ```

- **Si estás corriendo con `uv` en local:**
  ```powershell
  uv run python scripts/seed_demo.py
  ```

---

## 🔑 Credenciales para Pruebas

Tras ejecutar el seed, dispones de los siguientes datos listos para iniciar sesión:

### Tenant 1: `banco-andino`
- **Slug del Tenant:** `banco-andino`
- **Administrador:**
  - Email: `admin@banco-andino.com`
  - Contraseña: `Admin123!`
- **Cliente 1 (Juan Pérez):**
  - Email: `juan@banco-andino.com`
  - Contraseña: `Cliente123!`
  - Cuentas activas: `1000000001` (COP $5.000.000), `1000000002` (USD $1.500)
- **Cliente 2 (Jaysir Martínez):**
  - Email: `jaysir@banco-andino.com`
  - Contraseña: `Cliente123!`
  - Cuentas activas: `1000000003` (COP $2.500.000), `1000000004` (EUR €800)

### Tenant 2: `cooperativa-caribe`
- **Slug del Tenant:** `cooperativa-caribe`
- **Administrador:**
  - Email: `admin@coop-caribe.com`
  - Contraseña: `Admin123!`
- **Cliente 1:** `cliente1@coop-caribe.com` / `Cliente123!`
- **Cliente 2:** `cliente2@coop-caribe.com` / `Cliente123!`

---

## 📱 Configuración de Conexión en Flutter (Frontend)

El archivo `lib/core/network/dio_provider.dart` en `bankos-mobile` gestiona automáticamente la URL según la plataforma:

| Plataforma | URL por defecto | Nota |
|---|---|---|
| **Windows Desktop** | `http://localhost:8000/api/v1` | Automático al ejecutar `flutter run -d windows` |
| **Navegador Web** (Chrome/Edge) | `http://localhost:8000/api/v1` | Automático al ejecutar `flutter run -d chrome` |
| **Emulador Android** | `http://10.0.2.2:8000/api/v1` | Automático (`10.0.2.2` redirige al `localhost` del host) |
| **Dispositivo Físico** (Móvil real) | `http://<IP_DE_TU_PC>:8000/api/v1` | Debe pasarse por parámetro al compilar |

### Ejemplo para probar en móvil físico por Wi-Fi:
1. Obtén la IP local de tu PC ejecutando en PowerShell: `ipconfig` (ej: `192.168.1.15`).
2. Ejecuta Flutter especificando la variable:
   ```powershell
   flutter run --dart-define=API_BASE_URL=http://192.168.1.15:8000/api/v1
   ```
*(Alternativa si el móvil está conectado por cable USB con depuración activa: ejecuta `adb reverse tcp:8000 tcp:8000` y el móvil podrá usar `http://localhost:8000/api/v1`).*

---

## 🌐 Endpoints y Documentación Interactiva

Endpoints disponibles tras levantar el servicio:

- **API Health Check**: [http://localhost:8000/api/v1/health](http://localhost:8000/api/v1/health)
- **Documentación Swagger / OpenAPI**: [http://localhost:8000/api/v1/docs](http://localhost:8000/api/v1/docs)
- **Documentación ReDoc**: [http://localhost:8000/api/v1/redoc](http://localhost:8000/api/v1/redoc)

### Verificación Rápida por PowerShell:
```powershell
# Probar el estado de salud del backend
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/health" -Method Get

# Probar el inicio de sesión vía API
$login = @{ tenant_slug = "banco-andino"; email = "juan@banco-andino.com"; password = "Cliente123!" } | ConvertTo-Json
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/auth/login" -Method Post -Body $login -ContentType "application/json"
```

---

## 🚨 Solución de Errores Frecuentes (Troubleshooting)

### Error 1: `401 Unauthorized` al intentar iniciar sesión
- **Causa:** La base de datos fue creada pero no contiene registros (está vacía), o el `tenant_slug` no coincide.
- **Solución:** Ejecutar el seed:
  ```powershell
  docker exec bankos_api python scripts/seed_demo.py
  ```

### Error 2: `[Errno 10048] address already in use` al levantar con Uvicorn
- **Causa:** El puerto `8000` ya está siendo utilizado por el contenedor Docker `bankos_api`.
- **Solución:**
  - Si quieres usar Docker: No necesitas correr `uvicorn` en tu máquina; el contenedor ya está sirviendo la API.
  - Si quieres correr con `uv` en tu máquina: Detén el contenedor de la API dejando solo la base de datos y Redis:
    ```powershell
    docker stop bankos_api
    uv run uvicorn app.main:app --reload --port 8000
    ```

### Error 3: `Cannot connect to host db:5432` o `gaierror 11001` al ejecutar en local
- **Causa:** Tu archivo `.env` tiene `POSTGRES_SERVER="db"` y `REDIS_HOST="redis"`. Los nombres `db` y `redis` solo existen en la red interna de Docker.
- **Solución:** En tu archivo `.env`, cambia `db` y `redis` por `localhost`:
  ```ini
  POSTGRES_SERVER="localhost"
  REDIS_HOST="localhost"
  DATABASE_URL="postgresql+asyncpg://postgres:postgres_secure_password@localhost:5432/bankos_db"
  REDIS_URL="redis://localhost:6379/0"
  ```

### Error 4: `UniqueViolationError: duplicate key value violates unique constraint "tenants_pkey"`
- **Causa:** El script de seed se intentó ejecutar cuando los datos ya estaban cargados.
- **Solución:** El script `scripts/seed_demo.py` ahora cuenta con verificación de idempotencia y no fallará; si ya existen los datos simplemente mostrará el resumen de credenciales disponibles.

### Error 5: `ForeignKeyViolationError: insert or update on table "accounts" violates foreign key constraint`
- **Causa:** SQLAlchemy intentaba insertar cuentas antes de guardar los usuarios.
- **Solución:** Corregido en `scripts/seed_demo.py` con llamadas secuenciales a `session.flush()`.

### Error 6: `ValueError: Formatting field not found in record: 'correlation_id'`
- **Causa:** La librería de logging de Python no inyectaba automáticamente el campo de trazabilidad a registros generados por SQLAlchemy o Uvicorn.
- **Solución:** Corregido en `app/core/middlewares/correlation_id.py` usando `logging.setLogRecordFactory`.

### Error 7: `DioExceptionType.connectionError` en el emulador de Android
- **Causa:** El frontend intentó acceder a `http://localhost:8000` en lugar de `http://10.0.2.2:8000`, o el backend no está escuchando en `0.0.0.0`.
- **Solución:**
  1. Verifica que el backend esté corriendo escuchando en `0.0.0.0:8000` (no solo en `127.0.0.1`).
  2. Si ejecutas en emulador, `dio_provider.dart` ya detecta Android y usa `10.0.2.2`. Si persiste, fuerza la URL con:
     ```powershell
     flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8000/api/v1
     ```
