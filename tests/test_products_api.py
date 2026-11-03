"""Pruebas de integración HTTP + SQLite del recurso /products."""
import pytest

from tests.conftest import create_customer, create_product, order_payload


def test_create_and_get_product(client):
    created = create_product(client, price_cents=1500, stock=3, name="  Cuaderno  ")
    assert created == {"id": 1, "name": "Cuaderno", "price_cents": 1500, "stock": 3}
    assert client.get("/products/1").json() == created


def test_list_products_ordered_by_id(client):
    create_product(client, name="B")
    create_product(client, name="A")
    assert [p["name"] for p in client.get("/products").json()] == ["B", "A"]


def test_update_product_replaces_all_fields(client):
    create_product(client)
    response = client.put("/products/1", json={"name": "Nuevo", "price_cents": 999, "stock": 0})
    assert response.status_code == 200
    assert response.json() == {"id": 1, "name": "Nuevo", "price_cents": 999, "stock": 0}


@pytest.mark.parametrize("method, path", [("get", "/products/9"), ("delete", "/products/9")])
def test_missing_product_returns_404(client, method, path):
    response = getattr(client, method)(path)
    assert response.status_code == 404
    assert response.json()["detail"] == "Producto no encontrado"


def test_update_missing_product_returns_404(client):
    assert client.put("/products/9", json={"name": "X", "price_cents": 1, "stock": 1}).status_code == 404


@pytest.mark.parametrize("body", [
    {"name": "X", "price_cents": 0, "stock": 1},
    {"name": "X", "price_cents": 100, "stock": -1},
    {"name": "X", "price_cents": "100", "stock": 1},
    {"name": "", "price_cents": 100, "stock": 1},
    {"name": "X", "price_cents": 100, "stock": 1, "extra": True},
    {"name": "X", "price_cents": 100_000_001, "stock": 1},
])
def test_invalid_product_returns_422(client, body):
    assert client.post("/products", json=body).status_code == 422


def test_delete_product_returns_204_and_removes_it(client):
    create_product(client)
    response = client.delete("/products/1")
    assert response.status_code == 204
    assert response.content == b""
    assert client.get("/products/1").status_code == 404


def test_product_in_an_order_cannot_be_deleted(client):
    customer = create_customer(client)
    product = create_product(client)
    client.post("/orders", json=order_payload(customer["id"], [{"product_id": product["id"], "quantity": 1}]))
    response = client.delete(f"/products/{product['id']}")
    assert response.status_code == 409
    assert client.get(f"/products/{product['id']}").status_code == 200
