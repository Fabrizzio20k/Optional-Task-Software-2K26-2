"""Adicional 1: idempotencia de POST /orders mediante la cabecera Idempotency-Key."""
import threading

import pytest
from fastapi.testclient import TestClient

from app.orders import OrderInput, OrderService
from main import create_app
from tests.conftest import create_customer, create_product, order_payload


@pytest.fixture
def payload(client):
    customer = create_customer(client)
    product = create_product(client, stock=50)
    return order_payload(customer["id"], [{"product_id": product["id"], "quantity": 2}])


def post(client, payload, key):
    return client.post("/orders", json=payload, headers={"Idempotency-Key": key})


def test_same_key_and_content_returns_original_without_new_order(client, payload):
    first = post(client, payload, "pedido-abc")
    second = post(client, payload, "pedido-abc")
    assert first.status_code == second.status_code == 201
    assert second.json() == first.json()
    assert second.headers["Idempotent-Replayed"] == "true"
    assert "Idempotent-Replayed" not in first.headers
    assert len(client.get("/orders").json()) == 1


def test_same_key_with_different_content_is_a_conflict(client, payload):
    post(client, payload, "pedido-abc")
    payload["shipping"] = "express"
    response = post(client, payload, "pedido-abc")
    assert response.status_code == 409
    assert len(client.get("/orders").json()) == 1


def test_different_keys_create_different_orders(client, payload):
    assert post(client, payload, "k1").json()["id"] != post(client, payload, "k2").json()["id"]


def test_without_key_behaviour_is_unchanged(client, payload):
    client.post("/orders", json=payload)
    client.post("/orders", json=payload)
    assert len(client.get("/orders").json()) == 2


def test_empty_key_is_rejected(client, payload):
    assert post(client, payload, "").status_code == 422


def test_concurrent_requests_with_same_key_create_a_single_order(tmp_path):
    path = tmp_path / "concurrent.sqlite3"
    with TestClient(create_app(path)) as client:
        customer = create_customer(client)
        product = create_product(client, stock=50)
        service = OrderService(client.app.state.database)
    data = OrderInput(**order_payload(customer["id"], [{"product_id": product["id"], "quantity": 1}]))
    results, errors = [], []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        try:
            results.append(service.create(data, "misma-clave"))
        except Exception as error:  # pragma: no cover - solo se registra si algo falla
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len({result["id"] for result, _ in results}) == 1
    assert sum(1 for _, replayed in results if not replayed) == 1
    with TestClient(create_app(path)) as client:
        assert len(client.get("/orders").json()) == 1


def test_key_survives_restart(tmp_path):
    path = tmp_path / "restart.sqlite3"
    with TestClient(create_app(path)) as client:
        customer = create_customer(client)
        product = create_product(client)
        payload = order_payload(customer["id"], [{"product_id": product["id"], "quantity": 1}])
        original = post(client, payload, "reinicio").json()
    with TestClient(create_app(path)) as client:
        replay = post(client, payload, "reinicio")
        assert replay.json() == original
        assert replay.headers["Idempotent-Replayed"] == "true"
        assert len(client.get("/orders").json()) == 1


def test_failed_request_does_not_lock_the_key(client):
    product = create_product(client)
    payload = order_payload(1, [{"product_id": product["id"], "quantity": 1}])
    assert post(client, payload, "reintento").status_code == 404  # el cliente 1 aún no existe
    assert client.get("/orders").json() == []
    create_customer(client)
    retry = post(client, payload, "reintento")
    assert retry.status_code == 201
    assert "Idempotent-Replayed" not in retry.headers


def test_error_after_inserting_order_leaves_no_partial_order_or_key(client, payload, monkeypatch):
    def fail(*args):
        raise RuntimeError("fallo simulado al guardar las líneas")
    monkeypatch.setattr(OrderService, "_insert_lines", staticmethod(fail))
    with pytest.raises(RuntimeError):
        post(client, payload, "falla")
    monkeypatch.undo()
    assert client.get("/orders").json() == []
    assert post(client, payload, "falla").status_code == 201
