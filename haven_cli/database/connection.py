"""
Database connection management for Haven CLI.

Provides both synchronous and asynchronous database access:
- Synchronous: SQLAlchemy engine/Session for standard operations
- Asynchronous: databases library + aiosqlite for async operations

Based on backend/app/models/database.py with adaptations for CLI context.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager, asynccontextmanager
from pathlib import Path
from typing import Generator, AsyncGenerator, Optional

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, Session

# Optional async support
try:
    from databases import Database
    ASYNC_SUPPORT = True
except ImportError:
    ASYNC_SUPPORT = False
    Database = None  # type: ignore

from haven_cli.config import get_config, HavenConfig

logger = logging.getLogger(__name__)

# Global engine and session factory (lazy-loaded)
_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker] = None
_async_database: Optional["Database"] = None


def get_db_path(config: Optional[HavenConfig] = None) -> Path:
    """
    Get the database file path.
    
    Args:
        config: Haven configuration (uses global if not provided)
        
    Returns:
        Path to the SQLite database file
    """
    if config is None:
        config = get_config()
    
    # Extract path from database_url (sqlite:///path)
    db_url = config.database_url
    if db_url.startswith("sqlite:///"):
        return Path(db_url[10:])
    
    # Default fallback
    return config.data_dir / "haven.db"


def init_engine(config: Optional[HavenConfig] = None) -> Engine:
    """
    Initialize the SQLAlchemy engine.
    
    Args:
        config: Haven configuration (uses global if not provided)
        
    Returns:
        Configured SQLAlchemy engine
    """
    global _engine
    
    if _engine is not None:
        return _engine
    
    if config is None:
        config = get_config()
    
    # Ensure database directory exists
    db_path = get_db_path(config)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Create engine with SQLite-specific settings
    _engine = create_engine(
        config.database_url,
        connect_args={
            "check_same_thread": False,  # Allow cross-thread access
            "timeout": 30,  # Connection timeout in seconds
        },
        pool_pre_ping=True,  # Verify connections before use
        pool_recycle=3600,  # Recycle connections after 1 hour
        echo=False,  # Set to True for SQL debugging
    )
    
    # Enable foreign key support for SQLite
    @event.listens_for(_engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        """Enable SQLite foreign key support."""
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    
    logger.debug(f"Database engine initialized: {config.database_url}")
    return _engine


def get_session_maker(config: Optional[HavenConfig] = None) -> sessionmaker:
    """
    Get or create the session maker.
    
    Args:
        config: Haven configuration (uses global if not provided)
        
    Returns:
        Configured session maker
    """
    global _SessionLocal
    
    if _SessionLocal is not None:
        return _SessionLocal
    
    engine = init_engine(config)
    _SessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
    )
    
    return _SessionLocal


@contextmanager
def get_db_session(config: Optional[HavenConfig] = None) -> Generator[Session, None, None]:
    """
    Get a database session context manager.
    
    Usage:
        with get_db_session() as session:
            video = session.query(Video).first()
    
    Args:
        config: Haven configuration (uses global if not provided)
        
    Yields:
        SQLAlchemy Session
    """
    SessionLocal = get_session_maker(config)
    session = SessionLocal()
    
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Generator[Session, None, None]:
    """
    Get database session generator (for dependency injection).
    
    Same as get_db_session but as a generator for FastAPI-style DI.
    """
    with get_db_session() as session:
        yield session


# Async support
def init_async_db(config: Optional[HavenConfig] = None) -> Optional[Database]:
    """
    Initialize async database connection.
    
    Args:
        config: Haven configuration (uses global if not provided)
        
    Returns:
        databases.Database instance or None if async support not available
    """
    global _async_database
    
    if not ASYNC_SUPPORT:
        logger.warning("Async database support not available. Install 'databases' package.")
        return None
    
    if _async_database is not None:
        return _async_database
    
    if config is None:
        config = get_config()
    
    # Convert sqlite:/// to aiosqlite:///
    db_url = config.database_url
    if db_url.startswith("sqlite:///"):
        db_url = db_url.replace("sqlite:///", "aiosqlite:///", 1)
    
    _async_database = Database(db_url)
    logger.debug(f"Async database initialized: {db_url}")
    
    return _async_database


@asynccontextmanager
async def get_async_db_session(
    config: Optional[HavenConfig] = None
) -> AsyncGenerator[Database, None]:
    """
    Get async database session context manager.
    
    Usage:
        async with get_async_db_session() as db:
            row = await db.fetch_one("SELECT * FROM videos WHERE id = :id", {"id": 1})
    
    Args:
        config: Haven configuration (uses global if not provided)
        
    Yields:
        databases.Database connection
    """
    if not ASYNC_SUPPORT:
        raise RuntimeError(
            "Async database support not available. "
            "Install with: pip install databases aiosqlite"
        )
    
    db = init_async_db(config)
    
    if db is None:
        raise RuntimeError("Failed to initialize async database")
    
    async with db.connection() as conn:
        yield conn


async def connect_async_db(config: Optional[HavenConfig] = None) -> Optional[Database]:
    """
    Connect to async database.
    
    Call this at application startup.
    
    Args:
        config: Haven configuration (uses global if not provided)
        
    Returns:
        Connected Database instance or None
    """
    if not ASYNC_SUPPORT:
        return None
    
    db = init_async_db(config)
    
    if db is None:
        return None
    
    if not db.is_connected:
        await db.connect()
        logger.info("Async database connected")
    
    return db


async def disconnect_async_db() -> None:
    """Disconnect from async database. Call at application shutdown."""
    global _async_database
    
    if _async_database is not None and _async_database.is_connected:
        await _async_database.disconnect()
        logger.info("Async database disconnected")


def create_tables(config: Optional[HavenConfig] = None) -> None:
    """
    Create all database tables.
    
    This is typically called during application initialization
    or migration setup.
    
    Args:
        config: Haven configuration (uses global if not provided)
    """
    from haven_cli.database.models import Base
    
    engine = init_engine(config)
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created")


def drop_tables(config: Optional[HavenConfig] = None) -> None:
    """
    Drop all database tables.
    
    WARNING: This will delete all data!
    
    Args:
        config: Haven configuration (uses global if not provided)
    """
    from haven_cli.database.models import Base
    
    engine = init_engine(config)
    Base.metadata.drop_all(bind=engine)
    logger.warning("Database tables dropped")


def reset_database(config: Optional[HavenConfig] = None) -> None:
    """
    Reset database by dropping and recreating all tables.
    
    WARNING: This will delete all data!
    
    Args:
        config: Haven configuration (uses global if not provided)
    """
    drop_tables(config)
    create_tables(config)
    logger.warning("Database reset complete")
