import os
import asyncpg
from typing import AsyncGenerator, Optional
from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv()

# Database connection URL
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:admin123@localhost:5432/fitness_db"
)

# Global connection pool
_pool: Optional[asyncpg.Pool] = None

async def get_pool() -> asyncpg.Pool:
    """Get or create a connection pool."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=DATABASE_URL,
            min_size=1,
            max_size=10,
            command_timeout=60
        )
    return _pool

async def close_pool():
    """Close the connection pool."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None

# FastAPI dependency
async def get_db() -> AsyncGenerator[asyncpg.Connection, None]:
    """Dependency to get a database connection."""
    pool = await get_pool()
    print("pool", pool)
    async with pool.acquire() as conn:
        yield conn