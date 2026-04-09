-- payp test database seed
-- Creates a realistic sample schema for testing

-- Customers
CREATE TABLE customers (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    region VARCHAR(50) NOT NULL,
    segment VARCHAR(20) NOT NULL DEFAULT 'free',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE customers IS 'Customer accounts';
COMMENT ON COLUMN customers.segment IS 'free, starter, pro, enterprise';
COMMENT ON COLUMN customers.region IS 'EU-West, EU-East, NA, APAC';

-- Products
CREATE TABLE products (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    category VARCHAR(100) NOT NULL,
    price NUMERIC(12, 2) NOT NULL,
    active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE products IS 'Product catalog';

-- Orders
CREATE TABLE orders (
    id BIGSERIAL PRIMARY KEY,
    customer_id BIGINT NOT NULL REFERENCES customers(id),
    status SMALLINT NOT NULL DEFAULT 1,
    total_amount NUMERIC(12, 2) NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE orders IS 'Customer orders';
COMMENT ON COLUMN orders.status IS '1=pending, 2=confirmed, 3=shipped, 4=delivered, 5=cancelled';

CREATE INDEX idx_orders_customer_id ON orders(customer_id);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_orders_created_at ON orders(created_at);

-- Order items
CREATE TABLE order_items (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id BIGINT NOT NULL REFERENCES products(id),
    quantity INT NOT NULL DEFAULT 1,
    unit_price NUMERIC(12, 2) NOT NULL,
    total NUMERIC(12, 2) NOT NULL
);

CREATE INDEX idx_order_items_order_id ON order_items(order_id);

-- Payments
CREATE TABLE payments (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES orders(id),
    amount NUMERIC(12, 2) NOT NULL,
    payment_method VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    paid_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE payments IS 'Payment transactions';
COMMENT ON COLUMN payments.payment_method IS 'credit_card, bank_transfer, paypal';
COMMENT ON COLUMN payments.status IS 'pending, completed, failed, refunded';

CREATE INDEX idx_payments_order_id ON payments(order_id);

-- Insert sample data

INSERT INTO customers (name, email, region, segment) VALUES
    ('Alice Johnson', 'alice@example.com', 'NA', 'pro'),
    ('Bob Smith', 'bob@example.com', 'EU-West', 'enterprise'),
    ('Charlie Chen', 'charlie@example.com', 'APAC', 'starter'),
    ('Diana Mueller', 'diana@example.com', 'EU-East', 'pro'),
    ('Eve Williams', 'eve@example.com', 'NA', 'free'),
    ('Frank Tanaka', 'frank@example.com', 'APAC', 'enterprise'),
    ('Grace Kim', 'grace@example.com', 'EU-West', 'starter'),
    ('Henry Larsson', 'henry@example.com', 'EU-West', 'pro'),
    ('Ivy Patel', 'ivy@example.com', 'APAC', 'free'),
    ('Jack Novak', 'jack@example.com', 'EU-East', 'starter');

INSERT INTO products (name, category, price) VALUES
    ('Basic Plan', 'subscription', 9.99),
    ('Pro Plan', 'subscription', 29.99),
    ('Enterprise Plan', 'subscription', 99.99),
    ('API Credits 1K', 'credits', 5.00),
    ('API Credits 10K', 'credits', 40.00),
    ('Storage 100GB', 'addon', 15.00),
    ('Priority Support', 'addon', 49.99),
    ('Custom Integration', 'service', 299.99);

INSERT INTO orders (customer_id, status, total_amount, created_at) VALUES
    (1, 4, 29.99, '2025-01-15 10:00:00+00'),
    (2, 4, 149.98, '2025-01-20 14:30:00+00'),
    (1, 4, 5.00, '2025-02-01 09:00:00+00'),
    (3, 4, 9.99, '2025-02-10 11:00:00+00'),
    (4, 3, 44.99, '2025-03-01 16:00:00+00'),
    (2, 4, 99.99, '2025-03-15 08:00:00+00'),
    (5, 5, 9.99, '2025-03-20 12:00:00+00'),
    (6, 2, 399.98, '2025-04-01 10:00:00+00'),
    (1, 1, 29.99, '2025-04-02 09:00:00+00'),
    (7, 4, 29.99, '2025-02-15 13:00:00+00'),
    (8, 4, 79.98, '2025-03-05 15:00:00+00'),
    (3, 3, 45.00, '2025-03-25 11:00:00+00'),
    (9, 1, 9.99, '2025-04-01 08:00:00+00'),
    (10, 4, 29.99, '2025-01-25 10:00:00+00'),
    (4, 4, 99.99, '2025-02-20 14:00:00+00');

INSERT INTO order_items (order_id, product_id, quantity, unit_price, total) VALUES
    (1, 2, 1, 29.99, 29.99),
    (2, 3, 1, 99.99, 99.99),
    (2, 7, 1, 49.99, 49.99),
    (3, 4, 1, 5.00, 5.00),
    (4, 1, 1, 9.99, 9.99),
    (5, 2, 1, 29.99, 29.99),
    (5, 6, 1, 15.00, 15.00),
    (6, 3, 1, 99.99, 99.99),
    (7, 1, 1, 9.99, 9.99),
    (8, 8, 1, 299.99, 299.99),
    (8, 3, 1, 99.99, 99.99),
    (9, 2, 1, 29.99, 29.99),
    (10, 2, 1, 29.99, 29.99),
    (11, 2, 1, 29.99, 29.99),
    (11, 7, 1, 49.99, 49.99),
    (12, 5, 1, 40.00, 40.00),
    (12, 4, 1, 5.00, 5.00),
    (13, 1, 1, 9.99, 9.99),
    (14, 2, 1, 29.99, 29.99),
    (15, 3, 1, 99.99, 99.99);

INSERT INTO payments (order_id, amount, payment_method, status, paid_at) VALUES
    (1, 29.99, 'credit_card', 'completed', '2025-01-15 10:05:00+00'),
    (2, 149.98, 'bank_transfer', 'completed', '2025-01-21 09:00:00+00'),
    (3, 5.00, 'credit_card', 'completed', '2025-02-01 09:01:00+00'),
    (4, 9.99, 'paypal', 'completed', '2025-02-10 11:05:00+00'),
    (5, 44.99, 'credit_card', 'completed', '2025-03-01 16:10:00+00'),
    (6, 99.99, 'bank_transfer', 'completed', '2025-03-15 10:00:00+00'),
    (7, 9.99, 'paypal', 'failed', NULL),
    (8, 399.98, 'bank_transfer', 'pending', NULL),
    (10, 29.99, 'credit_card', 'completed', '2025-02-15 13:05:00+00'),
    (11, 79.98, 'credit_card', 'completed', '2025-03-05 15:10:00+00'),
    (12, 45.00, 'paypal', 'completed', '2025-03-25 11:05:00+00'),
    (14, 29.99, 'credit_card', 'completed', '2025-01-25 10:05:00+00'),
    (15, 99.99, 'bank_transfer', 'completed', '2025-02-20 14:30:00+00');
