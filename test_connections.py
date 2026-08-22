"""
Quick sanity check that Neon Postgres and Redis are reachable
before building any app logic on top of them.

Usage:
    python test_connections.py
"""

import os
from dotenv import load_dotenv

load_dotenv()


def test_postgres():
    from sqlalchemy import create_engine, text

    url = os.environ["DATABASE_URL"]
    engine = create_engine(url)
    with engine.connect() as conn:
        result = conn.execute(text("SELECT version();"))
        version = result.scalar()
        print(f"[Postgres] Connected. {version}")


def test_redis():
    import redis

    url = os.environ["REDIS_URL"]
    r = redis.from_url(url)
    r.set("blue_horizon_test", "ok")
    value = r.get("blue_horizon_test")
    print(f"[Redis] Connected. Test key value: {value.decode()}")
    r.delete("blue_horizon_test")


if __name__ == "__main__":
    try:
        test_postgres()
    except Exception as e:
        print(f"[Postgres] FAILED: {e}")

    try:
        test_redis()
    except Exception as e:
        print(f"[Redis] FAILED: {e}")