"""Pruebas de integración del ciclo de vida de pedidos (HTTP + SQLite)."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from main import create_app
from tests.conftest import create_customer, create_product, order_payload


@pytest.fixture
def setup(client):
    customer = create_customer(client, category="premium")
    product = create_product(client, price_cents=2500, stock=5)
    payload = order_payload(customer["id"], [{"product_id": product["id"], "quantity": 2}])
    return client, customer, product, payload


def test_create_order_stores_lines_and_totals(setup):
    client, customer, product, payload = setup
    response = client.post("/orders", json=payload)
    assert response.status_code == 201
    order = response.json()
    assert order["status"] == "pending"
    assert order["customer_category"] == "premium"
    assert (order["subtotal_cents"], order["discount_cents"], order["shipping_cents"], order["total_cents"]) == (5000, 500, 1000, 5500)
    assert order["items"] == [{"product_id": product["id"], "quantity": 2, "unit_price_cents": 2500}]


def test_quote_matches_created_order_and_does_not_reserve_stock(setup):
    client, _, product, payload = setup
    quote = client.post("/orders/quote", json=payload).json()
    order = client.post("/orders", json=payload).json()
    assert quote == {key: order[key] for key in quote}
    assert client.get(f"/products/{product['id']}").json()["stock"] == 5


def test_list_is_a_summary_and_detail_includes_items(setup):
    client, _, _, payload = setup
    client.post("/orders", json=payload)
    client.post("/orders", json=payload)
    listed = client.get("/orders").json()
    assert [o["id"] for o in listed] == [1, 2]
    assert "items" not in listed[0]
    assert len(client.get("/orders/1").json()["items"]) == 1


@pytest.mark.parametrize("change, detail", [
    ({"customer_id": 99}, "Cliente no encontrado"),
    ({"items": [{"product_id": 99, "quantity": 1}]}, "Producto 99 no encontrado"),
])
def test_create_or_quote_with_missing_references_returns_404(setup, change, detail):
    client, _, _, payload = setup
    payload.update(change)
    for path in ("/orders", "/orders/quote"):
        response = client.post(path, json=payload)
        assert response.status_code == 404
        assert response.json()["detail"] == detail
    assert client.get("/orders").json() == []


def test_saved_order_keeps_prices_and_category(setup):
    client, customer, product, payload = setup
    order = client.post("/orders", json=payload).json()
    client.put(f"/products/{product['id']}", json={"name": "Libro", "price_cents": 9000, "stock": 5})
    client.put(f"/customers/{customer['id']}", json={"name": "Ana", "email": customer["email"], "category": "regular"})
    assert client.get(f"/orders/{order['id']}").json() == order


def test_put_recalculates_with_current_prices_and_category(setup):
    client, customer, product, payload = setup
    order = client.post("/orders", json=payload).json()
    client.put(f"/products/{product['id']}", json={"name": "Libro", "price_cents": 6000, "stock": 5})
    client.put(f"/customers/{customer['id']}", json={"name": "Ana", "email": customer["email"], "category": "business"})
    payload["shipping"] = "express"
    response = client.put(f"/orders/{order['id']}", json=payload)
    assert response.status_code == 200
    updated = response.json()
    assert updated["id"] == order["id"]
    assert updated["customer_category"] == "business"
    assert (updated["subtotal_cents"], updated["discount_cents"], updated["shipping_cents"], updated["total_cents"]) == (12000, 1800, 2500, 12700)
    assert updated["items"] == [{"product_id": product["id"], "quantity": 2, "unit_price_cents": 6000}]


def test_put_replaces_lines(setup):
    client, _, product, payload = setup
    order = client.post("/orders", json=payload).json()
    other = create_product(client, price_cents=1000, name="Agenda")
    payload["items"] = [{"product_id": other["id"], "quantity": 1}]
    updated = client.put(f"/orders/{order['id']}", json=payload).json()
    assert updated["items"] == [{"product_id": other["id"], "quantity": 1, "unit_price_cents": 1000}]


def test_put_missing_order_returns_404(setup):
    client, _, _, payload = setup
    assert client.put("/orders/42", json=payload).status_code == 404


def test_cancel_pending_order_and_it_becomes_final(setup):
    client, _, product, payload = setup
    order = client.post("/orders", json=payload).json()
    response = client.post(f"/orders/{order['id']}/cancel")
    assert response.json() == {"id": order["id"], "status": "cancelled"}
    assert client.post(f"/orders/{order['id']}/cancel").status_code == 409
    assert client.post(f"/orders/{order['id']}/confirm").status_code == 409
    assert client.put(f"/orders/{order['id']}", json=payload).status_code == 409
    assert client.delete(f"/orders/{order['id']}").status_code == 409
    assert client.get(f"/products/{product['id']}").json()["stock"] == 5


def test_confirmed_order_is_final(setup):
    client, _, _, payload = setup
    order = client.post("/orders", json=payload).json()
    confirmed = client.post(f"/orders/{order['id']}/confirm").json()
    assert confirmed == {"id": order["id"], "status": "confirmed",
                         "message": f"Pedido {order['id']} confirmado. Total: 5500 centavos."}
    assert client.put(f"/orders/{order['id']}", json=payload).status_code == 409
    assert client.post(f"/orders/{order['id']}/cancel").status_code == 409
    assert client.delete(f"/orders/{order['id']}").status_code == 409
    assert client.get(f"/orders/{order['id']}").json()["status"] == "confirmed"


def test_confirm_creates_notification_for_customer(setup):
    client, customer, _, payload = setup
    order = client.post("/orders", json=payload).json()
    client.post(f"/orders/{order['id']}/confirm")
    notifications = client.get(f"/orders/{order['id']}/notifications").json()
    assert len(notifications) == 1
    assert notifications[0]["recipient"] == customer["email"]
    assert "5500" in notifications[0]["message"]


def test_delete_pending_order(setup):
    client, _, _, payload = setup
    order = client.post("/orders", json=payload).json()
    assert client.delete(f"/orders/{order['id']}").status_code == 204
    assert client.get(f"/orders/{order['id']}").status_code == 404


def test_confirm_with_exact_stock_leaves_zero(setup):
    client, _, product, payload = setup
    payload["items"][0]["quantity"] = 5
    order = client.post("/orders", json=payload).json()
    assert client.post(f"/orders/{order['id']}/confirm").status_code == 200
    assert client.get(f"/products/{product['id']}").json()["stock"] == 0


def test_second_order_fails_when_first_used_the_stock(setup):
    client, _, product, payload = setup
    payload["items"][0]["quantity"] = 3
    first = client.post("/orders", json=payload).json()
    second = client.post("/orders", json=payload).json()
    assert client.post(f"/orders/{first['id']}/confirm").status_code == 200
    response = client.post(f"/orders/{second['id']}/confirm")
    assert response.status_code == 409
    assert response.json()["detail"] == f"Stock insuficiente para producto {product['id']}"
    assert client.get(f"/products/{product['id']}").json()["stock"] == 2


@pytest.mark.parametrize("method, path", [
    ("get", "/orders/9"), ("delete", "/orders/9"), ("post", "/orders/9/confirm"),
    ("post", "/orders/9/cancel"), ("get", "/orders/9/notifications"),
])
def test_missing_order_returns_404(client, method, path):
    response = getattr(client, method)(path)
    assert response.status_code == 404
    assert response.json()["detail"] == "Pedido no encontrado"


def test_status_and_stock_survive_restart(tmp_path):
    path = tmp_path / "restart.sqlite3"
    with TestClient(create_app(path)) as client:
        customer = create_customer(client)
        product = create_product(client)
        order = client.post("/orders", json=order_payload(customer["id"], [{"product_id": product["id"], "quantity": 2}])).json()
        client.post(f"/orders/{order['id']}/confirm")
    with TestClient(create_app(path)) as client:
        assert client.get(f"/orders/{order['id']}").json()["status"] == "confirmed"
        assert client.get(f"/products/{product['id']}").json()["stock"] == 3
        assert client.get(f"/customers/{customer['id']}").json() == customer


def test_database_unavailable_returns_503(client, tmp_path):
    client.app.state.database.path = str(tmp_path)  # un directorio no es una base SQLite válida
    response = client.get("/products")
    assert response.status_code == 503
    assert "Base de datos no disponible" in response.json()["detail"]


def test_database_path_comes_from_environment(tmp_path, monkeypatch):
    target = tmp_path / "env.sqlite3"
    monkeypatch.setenv("LAB_DB_PATH", str(target))
    with TestClient(create_app()) as client:
        assert client.get("/orders").json() == []
    assert target.exists()


def test_failed_transaction_is_rolled_back(database):
    with pytest.raises(sqlite3.IntegrityError):
        with database.connect() as connection:
            connection.execute("INSERT INTO products(name, price_cents, stock) VALUES ('A', 100, 1)")
            connection.execute("INSERT INTO products(name, price_cents, stock) VALUES ('B', 0, 1)")
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0


def test_pickup_order_has_no_shipping_cost(setup):
    client, _, _, payload = setup
    payload["shipping"] = "pickup"
    order = client.post("/orders", json=payload).json()
    assert (order["shipping_cents"], order["total_cents"]) == (0, 4500)
