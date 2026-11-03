"""Fixtures compartidas: cada prueba usa su propia base SQLite temporal."""
import pytest
from fastapi.testclient import TestClient

from app.database import Database
from main import create_app


@pytest.fixture(autouse=True)
def no_background_worker(monkeypatch):
    """Las pruebas procesan la cola explícitamente para ser deterministas."""
    monkeypatch.setenv("LAB_WORKER", "0")


def drain_notifications(client):
    from app.notifications import NotificationWorker
    return NotificationWorker(client.app.state.database).drain()


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "test.sqlite3")) as session:
        yield session


@pytest.fixture
def database(tmp_path):
    db = Database(tmp_path / "unit.sqlite3")
    db.initialize()
    return db


def insert_customer(db, category="regular", email="cliente@example.com", name="Cliente"):
    with db.connect() as connection:
        return connection.execute(
            "INSERT INTO customers(name, email, category) VALUES (?, ?, ?)", (name, email, category),
        ).lastrowid


def insert_product(db, price_cents, stock=10, name="Producto"):
    with db.connect() as connection:
        return connection.execute(
            "INSERT INTO products(name, price_cents, stock) VALUES (?, ?, ?)", (name, price_cents, stock),
        ).lastrowid


def create_customer(client, email="ana@example.com", category="regular", name="Ana"):
    response = client.post("/customers", json={"name": name, "email": email, "category": category})
    assert response.status_code == 201
    return response.json()


def create_product(client, price_cents=2500, stock=5, name="Libro"):
    response = client.post("/products", json={"name": name, "price_cents": price_cents, "stock": stock})
    assert response.status_code == 201
    return response.json()


def order_payload(customer_id, items, shipping="standard"):
    return {"customer_id": customer_id, "address": "Av. Primavera 100", "city": "Lima",
            "postal_code": "15001", "shipping": shipping, "items": items}
