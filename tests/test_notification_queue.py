"""Adicional 2: cola de notificaciones, reintentos, DLQ, recuperación y duplicados."""
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.notifications import DEAD, DONE, PENDING, PROCESSING, NotificationWorker, SimulatedSender, WorkerThread
from main import create_app
from tests.conftest import create_customer, create_product, order_payload


class FakeClock:
    def __init__(self):
        self.now = 1_000.0

    def __call__(self):
        return self.now


def confirm_order_for(client, email):
    customer = create_customer(client, email=email)
    product = create_product(client)
    order = client.post("/orders", json=order_payload(customer["id"], [{"product_id": product["id"], "quantity": 1}])).json()
    assert client.post(f"/orders/{order['id']}/confirm").status_code == 200
    return order["id"]


def queue(client):
    return client.get("/notifications/queue").json()


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def worker(client, clock):
    clock.now = time.time() + 1  # un poco después de cualquier encolado de la prueba
    return NotificationWorker(client.app.state.database, max_attempts=3, base_delay=10, lease_seconds=30, clock=clock)


def test_confirm_leaves_a_pending_message_and_worker_delivers_it(client, worker):
    order_id = confirm_order_for(client, "ana@example.com")
    assert client.get(f"/orders/{order_id}/notifications").json() == []
    assert [m["status"] for m in queue(client)] == [PENDING]
    assert worker.process_one() is True
    assert queue(client)[0]["status"] == DONE
    delivered = client.get(f"/orders/{order_id}/notifications").json()
    assert len(delivered) == 1 and delivered[0]["recipient"] == "ana@example.com"
    assert worker.process_one() is False


def test_failed_confirmation_does_not_enqueue(client):
    customer = create_customer(client)
    product = create_product(client, stock=0)
    order = client.post("/orders", json=order_payload(customer["id"], [{"product_id": product["id"], "quantity": 1}])).json()
    assert client.post(f"/orders/{order['id']}/confirm").status_code == 409
    assert queue(client) == []


def test_temporary_failure_is_retried_with_backoff_then_succeeds(client, worker, clock):
    order_id = confirm_order_for(client, "ana+temporal2@example.com")
    worker.process_one()                       # intento 1 falla
    message = queue(client)[0]
    assert (message["status"], message["attempts"]) == (PENDING, 1)
    assert message["next_attempt_at"] == pytest.approx(clock.now + 10)   # espera 10 s
    assert "Timeout" in message["last_error"]
    assert worker.process_one() is False       # aún no vence la espera
    clock.now += 10
    worker.process_one()                       # intento 2 falla, espera 20 s
    assert queue(client)[0]["next_attempt_at"] == pytest.approx(clock.now + 20)
    clock.now += 20
    worker.process_one()                       # intento 3 funciona
    assert queue(client)[0]["status"] == DONE
    assert len(client.get(f"/orders/{order_id}/notifications").json()) == 1


def test_exhausted_retries_go_to_dlq_with_content_attempts_and_cause(client, worker, clock):
    confirm_order_for(client, "ana+siempre@example.com")
    for _ in range(3):
        worker.process_one()
        clock.now += 100
    assert queue(client)[0]["status"] == DEAD
    [entry] = client.get("/notifications/dlq").json()
    assert entry["attempts"] == 3
    assert entry["recipient"] == "ana+siempre@example.com"
    assert "confirmado" in entry["message"]
    assert entry["last_error"].startswith("intentos agotados")


def test_permanent_failure_goes_straight_to_dlq(client, worker):
    confirm_order_for(client, "ana+permanente@example.com")
    worker.process_one()
    [entry] = client.get("/notifications/dlq").json()
    assert entry["attempts"] == 1
    assert entry["last_error"].startswith("permanente")


def test_dlq_message_can_be_reprocessed_explicitly(client, worker, clock):
    order_id = confirm_order_for(client, "ana+permanente@example.com")
    worker.process_one()
    dlq_id = client.get("/notifications/dlq").json()[0]["id"]
    worker.sender = SimulatedSender()
    # se “corrige” el destinatario antes de reprocesar
    with client.app.state.database.connect() as connection:
        connection.execute("UPDATE notification_queue SET recipient = 'ana@example.com'")
    response = client.post(f"/notifications/dlq/{dlq_id}/reprocess")
    assert response.status_code == 200
    assert client.get("/notifications/dlq").json() == []
    assert queue(client)[0]["status"] == PENDING and queue(client)[0]["attempts"] == 0
    clock.now = time.time() + 1
    worker.process_one()
    assert len(client.get(f"/orders/{order_id}/notifications").json()) == 1
    assert client.post("/notifications/dlq/999/reprocess").status_code == 404


def test_pending_messages_survive_restart(tmp_path):
    path = tmp_path / "restart.sqlite3"
    with TestClient(create_app(path)) as client:
        order_id = confirm_order_for(client, "ana@example.com")
    with TestClient(create_app(path)) as client:
        assert queue(client)[0]["status"] == PENDING
        assert client.post("/notifications/process").json() == {"processed": 1}
        assert len(client.get(f"/orders/{order_id}/notifications").json()) == 1


def test_message_stuck_in_processing_is_recovered_after_lease(client, worker, clock):
    order_id = confirm_order_for(client, "ana@example.com")
    claimed = worker.claim_next()              # el worker toma el mensaje y “se cae”
    assert claimed["status"] == PROCESSING
    assert worker.process_one() is False       # dentro del lease nadie más lo toma
    clock.now += 31                            # vence el lease
    assert worker.process_one() is True
    message = queue(client)[0]
    assert (message["status"], message["attempts"]) == (DONE, 2)
    assert len(client.get(f"/orders/{order_id}/notifications").json()) == 1


def test_processing_a_duplicate_does_not_duplicate_the_effect(client, worker):
    order_id = confirm_order_for(client, "ana@example.com")
    message = worker.claim_next()
    worker._mark_delivered(message)
    worker._mark_delivered(message)            # mismo mensaje entregado dos veces
    assert len(client.get(f"/orders/{order_id}/notifications").json()) == 1


def test_background_worker_thread_delivers(tmp_path):
    with TestClient(create_app(tmp_path / "bg.sqlite3", start_worker=True)) as client:
        order_id = confirm_order_for(client, "ana@example.com")
        deadline = time.time() + 5
        while time.time() < deadline and not client.get(f"/orders/{order_id}/notifications").json():
            time.sleep(0.1)
        assert len(client.get(f"/orders/{order_id}/notifications").json()) == 1


def test_worker_thread_stops_cleanly(database):
    thread = WorkerThread(NotificationWorker(database), poll_interval=0.01)
    thread.start()
    thread.stop()
    assert not thread.thread.is_alive()


def test_simulated_sender_rules():
    sender = SimulatedSender()
    sender.send({"recipient": "a@x.com"}, 1)
    sender.send({"recipient": "a+temporal1@x.com"}, 2)
    with pytest.raises(Exception):
        sender.send({"recipient": "a+temporal1@x.com"}, 1)


def test_migration_adds_message_id_to_existing_notifications_table(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE notifications (id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, recipient TEXT NOT NULL, message TEXT NOT NULL)")
    connection.commit(); connection.close()
    with TestClient(create_app(path)):
        pass
    connection = sqlite3.connect(path)
    columns = [row[1] for row in connection.execute("PRAGMA table_info(notifications)")]
    connection.close()
    assert "message_id" in columns
