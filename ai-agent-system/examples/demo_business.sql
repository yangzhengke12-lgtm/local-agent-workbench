DROP TABLE IF EXISTS customers;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS tickets;

CREATE TABLE customers (
  customer_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  tier TEXT NOT NULL,
  city TEXT NOT NULL,
  monthly_revenue INTEGER NOT NULL
);

CREATE TABLE orders (
  order_id TEXT PRIMARY KEY,
  customer_id TEXT NOT NULL,
  status TEXT NOT NULL,
  amount INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(customer_id) REFERENCES customers(customer_id)
);

CREATE TABLE tickets (
  ticket_id TEXT PRIMARY KEY,
  customer_id TEXT NOT NULL,
  priority TEXT NOT NULL,
  status TEXT NOT NULL,
  title TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(customer_id) REFERENCES customers(customer_id)
);

INSERT INTO customers VALUES
  ('cust_001', 'Northwind Retail', 'enterprise', 'Shanghai', 128000),
  ('cust_002', 'River Studio', 'growth', 'Hangzhou', 42000),
  ('cust_003', 'Blue Peak Logistics', 'enterprise', 'Shenzhen', 96000),
  ('cust_004', 'Tiny SaaS Lab', 'starter', 'Chengdu', 9000);

INSERT INTO orders VALUES
  ('ord_1001', 'cust_001', 'paid', 8800, '2026-06-18'),
  ('ord_1002', 'cust_002', 'pending', 3200, '2026-06-19'),
  ('ord_1003', 'cust_003', 'refunded', 7600, '2026-06-20'),
  ('ord_1004', 'cust_001', 'paid', 11400, '2026-06-20');

INSERT INTO tickets VALUES
  ('ticket_9001', 'cust_001', 'high', 'open', 'API callback timeout during invoice sync', '2026-06-20'),
  ('ticket_9002', 'cust_003', 'medium', 'triaged', 'Refund webhook failed for one order', '2026-06-20'),
  ('ticket_9003', 'cust_004', 'low', 'closed', 'Need onboarding checklist', '2026-06-19');
