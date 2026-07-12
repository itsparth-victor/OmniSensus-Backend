from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.config import settings
import re

# Convert postgres:// or postgresql:// to postgresql+asyncpg://
def make_async_url(url: str) -> str:
    url = re.sub(r"^postgres(ql)?://", "postgresql+asyncpg://", url)
    # Remove sslmode and channel_binding from URL - asyncpg handles SSL differently
    url = re.sub(r"\?.*$", "", url)
    return url

ASYNC_DB_URL = make_async_url(settings.DATABASE_URL)

engine = create_async_engine(
    ASYNC_DB_URL,
    echo=settings.DEBUG,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    connect_args={
        "ssl": "require",
        "server_settings": {"application_name": "omnisensus_backend"},
    },
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

class Base(DeclarativeBase):
    pass

async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

async def check_db_connection() -> bool:
    try:
        async with engine.connect() as conn:
            from sqlalchemy import text
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        print(f"DB connection failed: {e}")
        return False
