"""Pruebas de integración HTTP + SQLite del recurso /customers."""
import pytest

from tests.conftest import create_customer, create_product, order_payload


def test_create_customer_defaults_to_regular(client):
    response = client.post("/customers", json={"name": "Luis", "email": "luis@example.com"})
    assert response.status_code == 201
    assert response.json() == {"id": 1, "name": "Luis", "email": "luis@example.com", "category": "regular"}


def test_get_and_list_customers(client):
    first = create_customer(client, email="a@example.com")
    second = create_customer(client, email="b@example.com")
    assert client.get("/customers/2").json() == second
    assert client.get("/customers").json() == [first, second]


def test_duplicate_email_returns_409(client):
    create_customer(client, email="ana@example.com")
    response = client.post("/customers", json={"name": "Otra", "email": "ana@example.com"})
    assert response.status_code == 409
    assert len(client.get("/customers").json()) == 1


def test_update_customer(client):
    create_customer(client)
    response = client.put("/customers/1", json={"name": "Ana M.", "email": "ana@example.com", "category": "business"})
    assert response.status_code == 200
    assert response.json()["category"] == "business"


def test_update_to_existing_email_returns_409(client):
    create_customer(client, email="a@example.com")
    create_customer(client, email="b@example.com")
    response = client.put("/customers/2", json={"name": "B", "email": "a@example.com"})
    assert response.status_code == 409
    assert client.get("/customers/2").json()["email"] == "b@example.com"


@pytest.mark.parametrize("method, path, body", [
    ("get", "/customers/5", None),
    ("put", "/customers/5", {"name": "X", "email": "x@example.com"}),
    ("delete", "/customers/5", None),
])
def test_missing_customer_returns_404(client, method, path, body):
    kwargs = {"json": body} if body else {}
    response = getattr(client, method)(path, **kwargs)
    assert response.status_code == 404
    assert response.json()["detail"] == "Cliente no encontrado"


@pytest.mark.parametrize("body", [
    {"name": "X", "email": "no-es-correo"},
    {"name": "X", "email": "x@example.com", "category": "vip"},
    {"name": "", "email": "x@example.com"},
    {"name": "X", "email": "x@example.com", "phone": "999"},
])
def test_invalid_customer_returns_422(client, body):
    assert client.post("/customers", json=body).status_code == 422


def test_delete_customer(client):
    create_customer(client)
    assert client.delete("/customers/1").status_code == 204
    assert client.get("/customers/1").status_code == 404


def test_customer_with_orders_cannot_be_deleted(client):
    customer = create_customer(client)
    product = create_product(client)
    client.post("/orders", json=order_payload(customer["id"], [{"product_id": product["id"], "quantity": 1}]))
    assert client.delete(f"/customers/{customer['id']}").status_code == 409
    assert client.get(f"/customers/{customer['id']}").status_code == 200
