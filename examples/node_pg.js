/**
 * Dual-pool PostgreSQL access through HAProxy + PgBouncer.
 * npm install pg
 */
const { Pool } = require("pg");

const host = process.env.DB_HOST || "haproxy.internal";
const common = {
  database: process.env.DB_NAME || "app",
  user: process.env.DB_USER || "app_user",
  password: process.env.DB_PASSWORD || "",
  ssl: { rejectUnauthorized: true },
  max: 20,
};

// Port 5000 -> always the current primary.
const writePool = new Pool({ ...common, host, port: 5000, max: 10 });
// Port 5001 -> round-robins live replicas.
const readPool = new Pool({ ...common, host, port: 5001, max: 20 });

async function createOrder(id, amount) {
  await writePool.query("INSERT INTO orders (id, amount) VALUES ($1, $2)", [id, amount]);
}

async function listRecentOrders() {
  // Fine for eventually-consistent reads; replicas can lag a little.
  const { rows } = await readPool.query(
    "SELECT id, amount FROM orders ORDER BY id DESC LIMIT 20"
  );
  return rows;
}

async function getOrderAfterWrite(id) {
  // Read-your-writes: use the write pool right after a write, not a replica.
  const { rows } = await writePool.query("SELECT id, amount FROM orders WHERE id = $1", [id]);
  return rows[0];
}

module.exports = { createOrder, listRecentOrders, getOrderAfterWrite };
