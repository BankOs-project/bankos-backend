from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

# Creación del motor asíncrono de base de datos
engine = create_async_engine(
    settings.async_database_url,
    echo=settings.DEBUG,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

# Fábrica de sesiones asíncronas
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


class Base(AsyncAttrs, DeclarativeBase):
    """Clase base declarativa para todos los modelos ORM de BankOS."""

    __abstract__ = True


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Inyector de dependencias FastAPI para obtener una sesión asíncrona de base de datos.
    
    Garantiza que la sesión se cierre adecuadamente y que se ejecute rollback
    automático en caso de excepciones no capturadas.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

