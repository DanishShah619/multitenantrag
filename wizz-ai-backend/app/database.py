from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import get_settings

settings = get_settings()

# pool_pre_ping avoids "server closed the connection" errors from Supabase's
# free-tier connection pooler dropping idle connections.
engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI dependency - one session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
