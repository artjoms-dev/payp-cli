-- payp test database seed - MySQL 8.0
-- Creates the same sample schema as seed.sql, adapted for MySQL dialect.

CREATE TABLE customers (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    region VARCHAR(50) NOT NULL,
    segment VARCHAR(20) NOT NULL DEFAULT 'free',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) COMMENT = 'Customer accounts';

CREATE TABLE products (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    category VARCHAR(100) NOT NULL,
    price DECIMAL(12, 2) NOT NULL,
    active TINYINT(1) NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) COMMENT = 'Product catalog';

CREATE TABLE orders (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    customer_id BIGINT NOT NULL,
    status SMALLINT NOT NULL DEFAULT 1,
    total_amount DECIMAL(12, 2) NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_orders_customer FOREIGN KEY (customer_id) REFERENCES customers(id)
) COMMENT = 'Customer orders';

CREATE INDEX idx_orders_customer_id ON orders(customer_id);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_orders_created_at ON orders(created_at);

CREATE TABLE order_items (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    order_id BIGINT NOT NULL,
    product_id BIGINT NOT NULL,
    quantity INT NOT NULL DEFAULT 1,
    unit_price DECIMAL(12, 2) NOT NULL,
    total DECIMAL(12, 2) NOT NULL,
    CONSTRAINT fk_items_order FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
    CONSTRAINT fk_items_product FOREIGN KEY (product_id) REFERENCES products(id)
);

CREATE INDEX idx_order_items_order_id ON order_items(order_id);

CREATE TABLE payments (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    order_id BIGINT NOT NULL,
    amount DECIMAL(12, 2) NOT NULL,
    payment_method VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    paid_at TIMESTAMP NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_payments_order FOREIGN KEY (order_id) REFERENCES orders(id)
) COMMENT = 'Payment transactions';

CREATE INDEX idx_payments_order_id ON payments(order_id);

-- Sample data

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
    (1, 4, 29.99, '2025-01-15 10:00:00'),
    (2, 4, 149.98, '2025-01-20 14:30:00'),
    (1, 4, 5.00, '2025-02-01 09:00:00'),
    (3, 4, 9.99, '2025-02-10 11:00:00'),
    (4, 3, 44.99, '2025-03-01 16:00:00'),
    (2, 4, 99.99, '2025-03-15 08:00:00'),
    (5, 5, 9.99, '2025-03-20 12:00:00'),
    (6, 2, 399.98, '2025-04-01 10:00:00'),
    (1, 1, 29.99, '2025-04-02 09:00:00'),
    (7, 4, 29.99, '2025-02-15 13:00:00'),
    (8, 4, 79.98, '2025-03-05 15:00:00'),
    (3, 3, 45.00, '2025-03-25 11:00:00'),
    (9, 1, 9.99, '2025-04-01 08:00:00'),
    (10, 4, 29.99, '2025-01-25 10:00:00'),
    (4, 4, 99.99, '2025-02-20 14:00:00');

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
    (1, 29.99, 'credit_card', 'completed', '2025-01-15 10:05:00'),
    (2, 149.98, 'bank_transfer', 'completed', '2025-01-21 09:00:00'),
    (3, 5.00, 'credit_card', 'completed', '2025-02-01 09:01:00'),
    (4, 9.99, 'paypal', 'completed', '2025-02-10 11:05:00'),
    (5, 44.99, 'credit_card', 'completed', '2025-03-01 16:10:00'),
    (6, 99.99, 'bank_transfer', 'completed', '2025-03-15 10:00:00'),
    (7, 9.99, 'paypal', 'failed', NULL),
    (8, 399.98, 'bank_transfer', 'pending', NULL),
    (10, 29.99, 'credit_card', 'completed', '2025-02-15 13:05:00'),
    (11, 79.98, 'credit_card', 'completed', '2025-03-05 15:10:00'),
    (12, 45.00, 'paypal', 'completed', '2025-03-25 11:05:00'),
    (14, 29.99, 'credit_card', 'completed', '2025-01-25 10:05:00'),
    (15, 99.99, 'bank_transfer', 'completed', '2025-02-20 14:30:00');
