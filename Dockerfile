FROM python:3.12-slim

# Instalar binario oficial de uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Configuración del entorno de ejecución
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=1

WORKDIR /app

# Instalar dependencias del sistema mínimas requeridas
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copiar archivos de dependencias
COPY pyproject.toml .python-version ./

# Instalar dependencias a nivel de sistema con uv
RUN uv pip install --system -r pyproject.toml

# Copiar el código fuente de la aplicación
COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

