"""
Database engine and session setup for Blue Horizon.
"""

import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()
DATABASE_URL = os.environ["DATABASE_URL"]

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

def get_db():
    """FastAPI dependency: yields a session and closes it after use."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()