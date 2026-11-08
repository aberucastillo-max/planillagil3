from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from .config import database_url

_url = database_url()
if _url.startswith("sqlite"):  # solo pruebas locales
    engine = create_engine(_url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
else:
    engine = create_engine(_url, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
