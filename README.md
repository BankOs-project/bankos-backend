# BankOS — Backend (Monolito Modular + Clean Architecture)

Backend de **BankOS** (Sistema Bancario Multitenant como Servicio), desarrollado con **Python 3.12**, **FastAPI**, **SQLAlchemy 2.0 (Async)**, **PostgreSQL 15**, **Redis 7** y administrado con **uv**.

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
- `security.py`: Generación/validación de JWT con claims obligatorios (`tenant_id`, `user_id`, `role`) y hashing seguro de contraseñas.
- `responses.py` & `exceptions.py`: Envelope único estandarizado (`success`, `internal_code`, `message`, `data`) y manejadores globales de error.
- `middlewares/correlation_id.py`: Generación, propagación en `ContextVar` e inyección del encabezado `X-Correlation-ID`.

---

## 🚀 Despliegue con Docker Compose

Para levantar toda la infraestructura (API, PostgreSQL 15 y Redis 7):

```bash
docker compose up --build
```

Endpoints disponibles tras levantar:
- **API Health Check**: [http://localhost:8000/api/v1/health](http://localhost:8000/api/v1/health)
- **Documentación Swagger / OpenAPI**: [http://localhost:8000/api/v1/docs](http://localhost:8000/api/v1/docs)
- **Documentación ReDoc**: [http://localhost:8000/api/v1/redoc](http://localhost:8000/api/v1/redoc)

---

## 🛠️ Desarrollo Local con `uv`

1. Instalar dependencias:
   ```bash
   uv sync
   ```
2. Iniciar servidor de desarrollo:
   ```bash
   uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

