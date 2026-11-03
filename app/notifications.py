"""Adicional 2: cola de notificaciones en SQLite con worker, reintentos y DLQ.

Flujo:
  confirmar pedido --(misma transacción)--> notification_queue (pending)
  worker: reclama un mensaje (processing + lease) -> intenta enviarlo ->
      éxito             -> registra el efecto en `notifications` (único por mensaje) y marca done
      fallo temporal    -> vuelve a pending con espera exponencial, hasta MAX_ATTEMPTS
      fallo permanente  -> pasa a la DLQ (notification_dlq) con contenido, intentos y causa
      intentos agotados -> pasa a la DLQ
Garantía: al menos una vez. Los duplicados no repiten el efecto porque
`notifications.message_id` es único (INSERT OR IGNORE).
"""
import os
import threading
import time

from fastapi import APIRouter, HTTPException, Request

MAX_ATTEMPTS = int(os.getenv("NOTIFY_MAX_ATTEMPTS", "3"))
BASE_DELAY_SECONDS = float(os.getenv("NOTIFY_BASE_DELAY", "1.0"))
LEASE_SECONDS = float(os.getenv("NOTIFY_LEASE_SECONDS", "30"))

PENDING, PROCESSING, DONE, DEAD = "pending", "processing", "done", "dead"


class TemporaryDeliveryError(Exception):
    """Fallo que vale la pena reintentar (p. ej. servicio de correo caído)."""


class PermanentDeliveryError(Exception):
    """Fallo que no se arregla reintentando (p. ej. destinatario inválido)."""


class SimulatedSender:
    """Simulación reproducible del envío, controlada por el correo del destinatario.

    - `...+temporal<N>@...`  falla temporalmente en los primeros N intentos y luego funciona.
    - `...+siempre@...`      falla temporalmente siempre (termina en la DLQ al agotar intentos).
    - `...+permanente@...`   falla de forma permanente en el primer intento.
    - cualquier otro correo  se entrega a la primera.
    """

    def send(self, message, attempt):
        local = message["recipient"].split("@", 1)[0]
        tag = local.split("+", 1)[1] if "+" in local else ""
        if tag == "permanente":
            raise PermanentDeliveryError("Destinatario rechazado (simulado)")
        if tag == "siempre":
            raise TemporaryDeliveryError("Servicio de correo no disponible (simulado)")
        if tag.startswith("temporal") and attempt <= int(tag.removeprefix("temporal") or 1):
            raise TemporaryDeliveryError(f"Timeout del servicio de correo, intento {attempt} (simulado)")


def enqueue(connection, order_id, recipient, message):
    """Se llama dentro de la transacción de confirmación: o se confirman ambos o ninguno."""
    now = time.time()
    connection.execute(
        """INSERT INTO notification_queue(order_id, recipient, message, status, attempts,
           next_attempt_at, created_at, updated_at) VALUES (?, ?, ?, ?, 0, ?, ?, ?)""",
        (order_id, recipient, message, PENDING, now, now, now),
    )


class NotificationWorker:
    def __init__(self, database, sender=None, max_attempts=MAX_ATTEMPTS, base_delay=BASE_DELAY_SECONDS,
                 lease_seconds=LEASE_SECONDS, clock=time.time):
        self.database = database
        self.sender = sender or SimulatedSender()
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.lease_seconds = lease_seconds
        self.clock = clock

    def claim_next(self):
        """Reclama el siguiente mensaje vencido, o uno en processing cuyo lease expiró (worker caído)."""
        now = self.clock()
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT * FROM notification_queue
                   WHERE (status = ? AND next_attempt_at <= ?) OR (status = ? AND locked_at <= ?)
                   ORDER BY id LIMIT 1""",
                (PENDING, now, PROCESSING, now - self.lease_seconds),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE notification_queue SET status = ?, locked_at = ?, attempts = attempts + 1, updated_at = ? WHERE id = ?",
                (PROCESSING, now, now, row["id"]),
            )
            return dict(connection.execute("SELECT * FROM notification_queue WHERE id = ?", (row["id"],)).fetchone())

    def process_one(self):
        message = self.claim_next()
        if message is None:
            return False
        try:
            self.sender.send(message, message["attempts"])
        except PermanentDeliveryError as error:
            self._dead_letter(message, f"permanente: {error}")
        except Exception as error:  # fallo temporal o inesperado
            if message["attempts"] >= self.max_attempts:
                self._dead_letter(message, f"intentos agotados: {error}")
            else:
                self._retry_later(message, str(error))
        else:
            self._mark_delivered(message)
        return True

    def drain(self, limit=1000):
        """Procesa todo lo vencido ahora (útil para pruebas y para el endpoint manual)."""
        processed = 0
        while processed < limit and self.process_one():
            processed += 1
        return processed

    def run(self, stop_event, poll_interval=0.5):
        while not stop_event.is_set():
            try:
                if not self.process_one():
                    stop_event.wait(poll_interval)
            except Exception:  # pragma: no cover - el worker no debe morir por un error puntual
                stop_event.wait(poll_interval)

    # --- transiciones -----------------------------------------------------

    def _mark_delivered(self, message):
        now = self.clock()
        with self.database.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO notifications(order_id, recipient, message, message_id) VALUES (?, ?, ?, ?)",
                (message["order_id"], message["recipient"], message["message"], message["id"]),
            )
            connection.execute(
                "UPDATE notification_queue SET status = ?, last_error = NULL, locked_at = NULL, updated_at = ? WHERE id = ?",
                (DONE, now, message["id"]),
            )

    def _retry_later(self, message, error):
        now = self.clock()
        delay = self.base_delay * 2 ** (message["attempts"] - 1)
        with self.database.connect() as connection:
            connection.execute(
                """UPDATE notification_queue SET status = ?, next_attempt_at = ?, last_error = ?,
                   locked_at = NULL, updated_at = ? WHERE id = ?""",
                (PENDING, now + delay, error, now, message["id"]),
            )

    def _dead_letter(self, message, error):
        now = self.clock()
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO notification_dlq(message_id, order_id, recipient, message, attempts, last_error, failed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (message["id"], message["order_id"], message["recipient"], message["message"],
                 message["attempts"], error, now),
            )
            connection.execute(
                "UPDATE notification_queue SET status = ?, last_error = ?, locked_at = NULL, updated_at = ? WHERE id = ?",
                (DEAD, error, now, message["id"]),
            )


def requeue_from_dlq(database, dlq_id, clock=time.time):
    """Reprocesamiento explícito: devuelve el mensaje a la cola con intentos en cero."""
    now = clock()
    with database.connect() as connection:
        entry = connection.execute("SELECT * FROM notification_dlq WHERE id = ?", (dlq_id,)).fetchone()
        if entry is None:
            raise HTTPException(404, "Mensaje no encontrado en la DLQ")
        connection.execute(
            """UPDATE notification_queue SET status = ?, attempts = 0, next_attempt_at = ?, last_error = NULL,
               locked_at = NULL, updated_at = ? WHERE id = ?""",
            (PENDING, now, now, entry["message_id"]),
        )
        connection.execute("DELETE FROM notification_dlq WHERE id = ?", (dlq_id,))
        return {"message_id": entry["message_id"], "status": PENDING}


class WorkerThread:
    """Un único worker en segundo plano, iniciado y detenido con la aplicación."""

    def __init__(self, worker, poll_interval=0.5):
        self.worker = worker
        self.poll_interval = poll_interval
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=worker.run, args=(self.stop_event, poll_interval), daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=5)


router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("/queue")
def list_queue(request: Request):
    with request.app.state.database.connect() as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM notification_queue ORDER BY id")]


@router.get("/dlq")
def list_dlq(request: Request):
    with request.app.state.database.connect() as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM notification_dlq ORDER BY id")]


@router.post("/dlq/{dlq_id}/reprocess")
def reprocess(dlq_id: int, request: Request):
    return requeue_from_dlq(request.app.state.database, dlq_id)


@router.post("/process")
def process_now(request: Request):
    """Procesa manualmente lo vencido (además del worker en segundo plano)."""
    return {"processed": NotificationWorker(request.app.state.database).drain()}
