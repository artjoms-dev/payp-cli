// payp test database seed — MongoDB 7
// Runs as root against payp_test database.
// Generates a realistic dataset: ~1000 customers, 1000 products, 1000 orders.

db = db.getSiblingDB('payp_test');

// Idempotent user creation: skip if user already exists (for re-seed scenarios).
const existingUsers = db.getUsers({filter: {user: 'payp'}}).users || [];
if (existingUsers.length === 0) {
  db.createUser({
    user: 'payp',
    pwd: 'payp_dev',
    roles: [{ role: 'readWrite', db: 'payp_test' }]
  });
}

// Idempotent: clear collections so re-running the seed is safe.
db.customers.drop();
db.products.drop();
db.orders.drop();

// ---- Deterministic pseudo-random so data is reproducible across runs ----
let _seed = 42;
function rand() {
  _seed = (_seed * 9301 + 49297) % 233280;
  return _seed / 233280;
}
function randInt(min, max) { return Math.floor(rand() * (max - min + 1)) + min; }
function pick(arr) { return arr[randInt(0, arr.length - 1)]; }
function daysAgo(n) { return new Date(Date.now() - n * 86400000); }

const firstNames = ['Alice','Bob','Clara','David','Eva','Frank','Grace','Hans','Ivy','Jack',
  'Kira','Liam','Mia','Noah','Olga','Pavel','Quinn','Rosa','Sven','Tina',
  'Uri','Vera','Wei','Xenia','Yara','Zane','Anya','Boris','Chen','Diana'];
const lastNames = ['Martin','Chen','Schmidt','Park','Rossi','Novak','Kumar','Silva','Petrov','Tanaka',
  'Johnson','Garcia','Muller','Dubois','Yilmaz','Singh','Nakamura','Okafor','Andersen','Kowalski'];
const regions = ['EU-West','EU-East','NA-East','NA-West','APAC','LATAM','MEA'];
const segments = ['free','starter','pro','enterprise'];
const segmentWeights = [0.40, 0.30, 0.20, 0.10];  // free dominates

function weightedPick(items, weights) {
  const r = rand();
  let cum = 0;
  for (let i = 0; i < items.length; i++) {
    cum += weights[i];
    if (r < cum) return items[i];
  }
  return items[items.length - 1];
}

// ---- Customers: 1000 rows ----
const N_CUSTOMERS = 1000;
const customers = [];
for (let i = 1; i <= N_CUSTOMERS; i++) {
  const fn = pick(firstNames);
  const ln = pick(lastNames);
  customers.push({
    customer_id: i,
    name: `${fn} ${ln}`,
    email: `${fn.toLowerCase()}.${ln.toLowerCase()}${i}@example.com`,
    region: pick(regions),
    segment: weightedPick(segments, segmentWeights),
    lifetime_value: Math.round(rand() * 50000 * 100) / 100,
    signup_source: pick(['organic','paid','referral','partner','direct']),
    is_active: rand() > 0.15,
    created_at: daysAgo(randInt(1, 730))
  });
}
db.customers.insertMany(customers);

// ---- Products: 1000 rows ----
const categories = ['software','addon','service','hardware','training','support'];
const productAdjs = ['Pro','Lite','Enterprise','Cloud','Edge','Smart','Ultra','Prime','Core','Elite'];
const productNouns = ['Pipeline','Analyzer','Connector','Monitor','Dashboard','Engine','Toolkit',
  'Framework','Platform','Gateway','Broker','Inspector','Profiler','Optimizer','Scheduler'];

const N_PRODUCTS = 1000;
const products = [];
for (let i = 1; i <= N_PRODUCTS; i++) {
  products.push({
    product_id: i,
    sku: `SKU-${String(i).padStart(6,'0')}`,
    name: `${pick(productAdjs)} ${pick(productNouns)} v${randInt(1,9)}`,
    category: pick(categories),
    price: Math.round((5 + rand() * 995) * 100) / 100,
    cost: Math.round((1 + rand() * 400) * 100) / 100,
    stock: randInt(0, 500),
    active: rand() > 0.10,
    created_at: daysAgo(randInt(1, 900))
  });
}
db.products.insertMany(products);

// ---- Orders: 1000 rows ----
const statuses = ['pending','completed','refunded','cancelled','shipped'];
const statusWeights = [0.15, 0.55, 0.08, 0.05, 0.17];

const N_ORDERS = 1000;
const orders = [];
for (let i = 1; i <= N_ORDERS; i++) {
  const cust = customers[randInt(0, N_CUSTOMERS - 1)];
  const prod = products[randInt(0, N_PRODUCTS - 1)];
  const qty = randInt(1, 5);
  orders.push({
    order_id: i,
    customer_id: cust.customer_id,
    customer_email: cust.email,
    product_id: prod.product_id,
    product: prod.name,
    quantity: qty,
    unit_price: prod.price,
    total: Math.round(prod.price * qty * 100) / 100,
    status: weightedPick(statuses, statusWeights),
    created_at: daysAgo(randInt(1, 365))
  });
}
db.orders.insertMany(orders);

// ---- Indexes ----
db.customers.createIndex({ email: 1 }, { unique: true });
db.customers.createIndex({ region: 1 });
db.customers.createIndex({ segment: 1 });
db.orders.createIndex({ customer_email: 1 });
db.orders.createIndex({ customer_id: 1 });
db.orders.createIndex({ status: 1 });
db.orders.createIndex({ created_at: -1 });
db.products.createIndex({ category: 1 });
db.products.createIndex({ sku: 1 }, { unique: true });

print('MongoDB seed complete: customers=' + db.customers.countDocuments() +
      ', products=' + db.products.countDocuments() +
      ', orders=' + db.orders.countDocuments());
