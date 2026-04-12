// payp test database seed - MongoDB 7
// Runs as root against payp_test database

db = db.getSiblingDB('payp_test');

db.createUser({
  user: 'payp',
  pwd: 'payp_dev',
  roles: [{ role: 'readWrite', db: 'payp_test' }]
});

// customers collection
db.customers.insertMany([
  { name: 'Alice Martin',    email: 'alice@example.com',  region: 'EU', segment: 'enterprise', created_at: new Date('2024-01-10') },
  { name: 'Bob Chen',        email: 'bob@example.com',    region: 'US', segment: 'startup',    created_at: new Date('2024-02-14') },
  { name: 'Clara Schmidt',   email: 'clara@example.com',  region: 'EU', segment: 'enterprise', created_at: new Date('2024-03-01') },
  { name: 'David Park',      email: 'david@example.com',  region: 'APAC', segment: 'smb',     created_at: new Date('2024-03-15') },
  { name: 'Eva Rossi',       email: 'eva@example.com',    region: 'EU', segment: 'smb',       created_at: new Date('2024-04-01') }
]);

// products collection
db.products.insertMany([
  { name: 'Data Pipeline Pro', category: 'software', price: 299.99, active: true },
  { name: 'ETL Toolkit',       category: 'software', price: 149.99, active: true },
  { name: 'SQL Analyzer',      category: 'software', price: 99.99,  active: false },
  { name: 'Cloud Connector',   category: 'addon',    price: 49.99,  active: true }
]);

// orders collection - with customer_email as FK-like reference
db.orders.insertMany([
  { customer_email: 'alice@example.com', product: 'Data Pipeline Pro', total: 299.99, status: 'completed', created_at: new Date('2024-02-01') },
  { customer_email: 'bob@example.com',   product: 'ETL Toolkit',       total: 149.99, status: 'completed', created_at: new Date('2024-03-10') },
  { customer_email: 'alice@example.com', product: 'Cloud Connector',   total: 49.99,  status: 'pending',   created_at: new Date('2024-04-05') },
  { customer_email: 'clara@example.com', product: 'Data Pipeline Pro', total: 299.99, status: 'completed', created_at: new Date('2024-04-10') },
  { customer_email: 'david@example.com', product: 'SQL Analyzer',      total: 99.99,  status: 'refunded',  created_at: new Date('2024-04-12') }
]);

// Create indexes
db.customers.createIndex({ email: 1 }, { unique: true });
db.orders.createIndex({ customer_email: 1 });
db.orders.createIndex({ status: 1 });

print('MongoDB seed complete: customers=' + db.customers.countDocuments() +
      ', products=' + db.products.countDocuments() +
      ', orders=' + db.orders.countDocuments());
