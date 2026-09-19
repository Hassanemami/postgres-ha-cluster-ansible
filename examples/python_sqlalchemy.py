"""
Dual-pool PostgreSQL access through HAProxy + PgBouncer.

pip install sqlalchemy psycopg2-binary
"""
import os
from sqlalchemy import create_engine, text

DB_HOST = os.environ.get("DB_HOST", "haproxy.internal")  # the VIP, or any HAProxy node
DB_NAME = os.environ.get("DB_NAME", "app")
DB_USER = os.environ.get("DB_USER", "app_user")
DB_PASS = os.environ.get("DB_PASSWORD", "")

# Port 5000 -> HAProxy's "master" listener -> always the current primary.
write_engine = create_engine(
    f"postgresql+psycopg2://{DB_USER}:{DB_PASS}@{DB_HOST}:5000/{DB_NAME}"
    "?sslmode=require",
    pool_pre_ping=True,     # detect a connection that died mid-failover
    pool_size=10,
    max_overflow=5,
)

# Port 5001 -> HAProxy's "replicas" listener -> round-robins live replicas.
read_engine = create_engine(
    f"postgresql+psycopg2://{DB_USER}:{DB_PASS}@{DB_HOST}:5001/{DB_NAME}"
    "?sslmode=require",
    pool_pre_ping=True,
    pool_size=20,
    max_overflow=10,
)


def create_order(order_id: int, amount: float) -> None:
    with write_engine.begin() as conn:
        conn.execute(
            text("INSERT INTO orders (id, amount) VALUES (:id, :amount)"),
            {"id": order_id, "amount": amount},
        )


def list_recent_orders():
    # Replicas can lag behind the primary by a few hundred ms. Fine for a
    # dashboard; NOT fine if you need to read your own write immediately
    # after create_order() - use write_engine for read-your-writes cases.
    with read_engine.connect() as conn:
        return conn.execute(
            text("SELECT id, amount FROM orders ORDER BY id DESC LIMIT 20")
        ).fetchall()


def get_order_after_write(order_id: int):
    # Read-your-writes: query the primary, not a replica, right after a write.
    with write_engine.connect() as conn:
        return conn.execute(
            text("SELECT id, amount FROM orders WHERE id = :id"),
            {"id": order_id},
        ).fetchone()
