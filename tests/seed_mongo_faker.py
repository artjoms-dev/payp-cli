"""Generate a realistic MongoDB dataset using Faker.

Usage:
    .venv/Scripts/python.exe tests/seed_mongo_faker.py
    .venv/Scripts/python.exe tests/seed_mongo_faker.py --customers 2000 --orders 5000

Assumes docker compose mongo service is running on localhost:27017
with root/root_dev credentials (see docker-compose.yml).
Creates/refreshes the `payp` user in `payp_test` for subsequent app use.
"""
from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta, timezone

from faker import Faker
from pymongo import ASCENDING, DESCENDING, MongoClient

ROOT_URI = "mongodb://root:root_dev@localhost:27017/?authSource=admin"
APP_USER = "payp"
APP_PWD = "payp_dev"
APP_DB = "payp_test"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Seed payp_test MongoDB with Faker data")
    p.add_argument("--customers", type=int, default=1000)
    p.add_argument("--products", type=int, default=1000)
    p.add_argument("--orders", type=int, default=1000)
    p.add_argument("--seed", type=int, default=42, help="RNG seed for reproducibility")
    return p.parse_args()


def ensure_user(client: MongoClient) -> None:
    """Create (or reset) the application user in APP_DB.

    Idempotent: if the user exists, we drop and recreate so password stays
    in sync with this script.
    """
    target = client[APP_DB]
    existing = target.command("usersInfo", {"user": APP_USER, "db": APP_DB})
    if existing.get("users"):
        target.command("dropUser", APP_USER)
    target.command(
        "createUser",
        APP_USER,
        pwd=APP_PWD,
        roles=[{"role": "readWrite", "db": APP_DB}],
    )


def build_customers(fake: Faker, n: int) -> list[dict]:
    regions = ["EU-West", "EU-East", "NA-East", "NA-West", "APAC", "LATAM", "MEA"]
    segments = ["free", "starter", "pro", "enterprise"]
    segment_weights = [0.40, 0.30, 0.20, 0.10]
    sources = ["organic", "paid", "referral", "partner", "direct"]
    now = datetime.now(timezone.utc)
    out = []
    for i in range(1, n + 1):
        first = fake.first_name()
        last = fake.last_name()
        # Ensure uniqueness by suffixing the row index.
        email = f"{first.lower()}.{last.lower()}{i}@{fake.free_email_domain()}"
        out.append({
            "customer_id": i,
            "name": f"{first} {last}",
            "email": email,
            "phone": fake.phone_number(),
            "region": random.choice(regions),
            "segment": random.choices(segments, segment_weights)[0],
            "country": fake.country_code(),
            "city": fake.city(),
            "company": fake.company(),
            "lifetime_value": round(random.uniform(0, 50000), 2),
            "signup_source": random.choice(sources),
            "is_active": random.random() > 0.15,
            "created_at": now - timedelta(days=random.randint(1, 730)),
        })
    return out


def build_products(fake: Faker, n: int) -> list[dict]:
    categories = ["software", "addon", "service", "hardware", "training", "support"]
    adjectives = ["Pro", "Lite", "Enterprise", "Cloud", "Edge", "Smart", "Ultra", "Prime", "Core", "Elite"]
    nouns = ["Pipeline", "Analyzer", "Connector", "Monitor", "Dashboard", "Engine",
             "Toolkit", "Framework", "Platform", "Gateway", "Broker", "Inspector",
             "Profiler", "Optimizer", "Scheduler"]
    now = datetime.now(timezone.utc)
    out = []
    for i in range(1, n + 1):
        price = round(random.uniform(5, 1000), 2)
        cost = round(price * random.uniform(0.2, 0.6), 2)
        out.append({
            "product_id": i,
            "sku": f"SKU-{i:06d}",
            "name": f"{random.choice(adjectives)} {random.choice(nouns)} v{random.randint(1, 9)}",
            "category": random.choice(categories),
            "description": fake.sentence(nb_words=10),
            "price": price,
            "cost": cost,
            "stock": random.randint(0, 500),
            "active": random.random() > 0.10,
            "created_at": now - timedelta(days=random.randint(1, 900)),
        })
    return out


def build_orders(n: int, customers: list[dict], products: list[dict]) -> list[dict]:
    statuses = ["pending", "completed", "refunded", "cancelled", "shipped"]
    status_weights = [0.15, 0.55, 0.08, 0.05, 0.17]
    now = datetime.now(timezone.utc)
    out = []
    for i in range(1, n + 1):
        cust = random.choice(customers)
        prod = random.choice(products)
        qty = random.randint(1, 5)
        out.append({
            "order_id": i,
            "customer_id": cust["customer_id"],
            "customer_email": cust["email"],
            "product_id": prod["product_id"],
            "product": prod["name"],
            "quantity": qty,
            "unit_price": prod["price"],
            "total": round(prod["price"] * qty, 2),
            "status": random.choices(statuses, status_weights)[0],
            "created_at": now - timedelta(days=random.randint(1, 365)),
        })
    return out


def main() -> None:
    args = parse_args()
    random.seed(args.seed)
    fake = Faker()
    Faker.seed(args.seed)

    print(f"Connecting to {ROOT_URI} ...")
    client: MongoClient = MongoClient(ROOT_URI, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")

    print(f"Ensuring app user '{APP_USER}' on db '{APP_DB}' ...")
    ensure_user(client)

    db = client[APP_DB]
    print("Dropping existing collections (customers, products, orders) ...")
    for coll in ("customers", "products", "orders"):
        db[coll].drop()

    print(f"Generating {args.customers} customers ...")
    customers = build_customers(fake, args.customers)
    db.customers.insert_many(customers)

    print(f"Generating {args.products} products ...")
    products = build_products(fake, args.products)
    db.products.insert_many(products)

    print(f"Generating {args.orders} orders ...")
    orders = build_orders(args.orders, customers, products)
    db.orders.insert_many(orders)

    print("Creating indexes ...")
    db.customers.create_index([("email", ASCENDING)], unique=True)
    db.customers.create_index([("region", ASCENDING)])
    db.customers.create_index([("segment", ASCENDING)])
    db.customers.create_index([("customer_id", ASCENDING)], unique=True)
    db.products.create_index([("sku", ASCENDING)], unique=True)
    db.products.create_index([("category", ASCENDING)])
    db.products.create_index([("product_id", ASCENDING)], unique=True)
    db.orders.create_index([("customer_email", ASCENDING)])
    db.orders.create_index([("customer_id", ASCENDING)])
    db.orders.create_index([("status", ASCENDING)])
    db.orders.create_index([("created_at", DESCENDING)])
    db.orders.create_index([("order_id", ASCENDING)], unique=True)

    print(
        f"Done. customers={db.customers.count_documents({})}, "
        f"products={db.products.count_documents({})}, "
        f"orders={db.orders.count_documents({})}"
    )
    client.close()


if __name__ == "__main__":
    main()
