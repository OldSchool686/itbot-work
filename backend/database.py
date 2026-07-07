import logging

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from backend.utils.config import settings

logger = logging.getLogger(__name__)

# Use psycopg (v3) native async driver — no compilation needed in slim images
db_url = settings.database_url.replace("postgresql://", "postgresql+psycopg://")
engine = create_async_engine(
    db_url,
    echo=settings.app_env == "development",
)

async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    """FastAPI dependency for database session."""
    session = async_session_factory()
    try:
        yield session
    except Exception:
        logger.exception("Database error during request, rolling back")
        await session.rollback()
        raise
    finally:
        await session.close()


async def init_db():
    """Initialize database tables (call on startup)."""
    async with engine.begin() as conn:
        pass  # Tables created via init.sql in docker-compose
