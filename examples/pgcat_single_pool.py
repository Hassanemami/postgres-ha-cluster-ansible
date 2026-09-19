"""
Single-pool PostgreSQL access through PgCat (Option A - see main README).
PgCat parses each query and routes it to the primary or a replica itself,
so the app only needs ONE pool.

pip install sqlalchemy psycopg2-binary
"""
import os
from sqlalchemy import create_engine, text

DB_HOST = os.environ.get("DB_HOST", "haproxy.internal")  # the VIP, or any node
DB_NAME = os.environ.get("DB_NAME", "app")
DB_USER = os.environ.get("DB_USER", "app_user")
DB_PASS = os.environ.get("DB_PASSWORD", "")

# Port 6433 -> PgCat, which decides per query where it goes.
# Note: PgCat's client-facing auth is MD5, not SCRAM - see docs/PGCAT.md.
engine = create_engine(
    f"postgresql+psycopg2://{DB_USER}:{DB_PASS}@{DB_HOST}:6433/{DB_NAME}"
    "?sslmode=require",
    pool_pre_ping=True,
    pool_size=20,
    max_overflow=10,
)


def create_order(order_id: int, amount: float) -> None:
    # A write - PgCat's query parser sends this to the primary.
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO orders (id, amount) VALUES (:id, :amount)"),
            {"id": order_id, "amount": amount},
        )


def list_recent_orders():
    # A read - PgCat's query parser sends this to a healthy replica.
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT id, amount FROM orders ORDER BY id DESC LIMIT 20")
        ).fetchall()


def get_order_after_write(order_id: int):
    # Read-your-writes: an explicit transaction forces PgCat to use the
    # primary for everything inside it (it can't see ahead to know later
    # statements won't write) - which is exactly what we want here, since a
    # replica might not have this row yet.
    with engine.begin() as conn:
        return conn.execute(
            text("SELECT id, amount FROM orders WHERE id = :id"),
            {"id": order_id},
        ).fetchone()
