from typing import Any
from urllib.parse import quote
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración global de la aplicación BankOS utilizando pydantic-settings.
    
    Carga variables desde el entorno o archivo .env, proveyendo valores por defecto
    seguros y validaciones para la infraestructura (PostgreSQL, Redis, JWT).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Identificación del Proyecto
    PROJECT_NAME: str = "BankOS API"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True

    # Seguridad y Criptografía (JWT)
    SECRET_KEY: str = "bankos-development-secret-key-change-this-in-production-min-32-chars-long!"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    RESEND_API_KEY: SecretStr | None = None
    REGISTRATION_EMAIL_FROM: str | None = None

    # Base de Datos Relacional (PostgreSQL 15)
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres_secure_password"
    POSTGRES_DB: str = "bankos_db"
    DATABASE_URL: str | None = None
    DATABASE_MANAGED_SCHEMA: bool = False
    RATE_LIMIT_TRUSTED_PROXIES: str = ""
    ACCOUNT_DEACTIVATION_COOLDOWN_HOURS: int = Field(default=24, ge=1)
    SIMULATED_INTERBANK_ENABLED: bool = False

    # Motor en Memoria (Redis 7)
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str | None = None
    REDIS_DB: int = 0
    REDIS_URL: str | None = None

    # CORS
    CORS_ORIGINS: list[str] = ["*"]

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Any) -> list[str]:
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",")]
        elif isinstance(v, (list, tuple)):
            return list(v)
        return ["*"]

    @property
    def async_database_url(self) -> str:
        """Retorna la URI asíncrona para SQLAlchemy usando asyncpg."""
        if self.DATABASE_URL:
            # Asegurar prefijo postgresql+asyncpg://
            if self.DATABASE_URL.startswith("postgresql://"):
                return self.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
            return self.DATABASE_URL
        return (
            f"postgresql+asyncpg://{quote(self.POSTGRES_USER, safe='')}:{quote(self.POSTGRES_PASSWORD, safe='')}"
            f"@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{quote(self.POSTGRES_DB, safe='')}"
        )

    @property
    def async_redis_url(self) -> str:
        """Retorna la URI de conexión para Redis."""
        if self.REDIS_URL:
            return self.REDIS_URL
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"


settings = Settings()

