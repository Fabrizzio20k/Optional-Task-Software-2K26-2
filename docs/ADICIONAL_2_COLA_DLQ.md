# Adicional 2 — Cola de notificaciones y dead letter queue

Rama: **`adicional-2-cola-dlq`** (parte de `main`; no incluye el adicional 1).

## Qué cambia

Antes, `POST /orders/{id}/confirm` insertaba la notificación directamente (síncrono).
Ahora, dentro de la **misma transacción** de la confirmación, se inserta un mensaje en `notification_queue` con estado `pending`. Un **worker** separado lo procesa después. Si la confirmación falla (por ejemplo, falta stock), no se encola nada.

`GET /orders/{id}/notifications` sigue mostrando las notificaciones **entregadas**; por eso, justo después de confirmar, puede estar vacío hasta que el worker procese el mensaje.

## Piezas (`app/notifications.py`)

| Pieza | Función |
| --- | --- |
| `notification_queue` (SQLite) | Mensajes con `status` (`pending`, `processing`, `done`, `dead`), `attempts`, `next_attempt_at`, `locked_at`, `last_error` |
| `notification_dlq` (SQLite) | Mensajes muertos: contenido, destinatario, intentos y causa del último error |
| `NotificationWorker` | `claim_next()`, `process_one()`, `drain()`, `run()` |
| `WorkerThread` | **Un único worker** en un hilo, iniciado y detenido con la app (`LAB_WORKER=1` por defecto) |
| `SimulatedSender` | Simulación reproducible de fallos según el correo del cliente |

## Reglas

| Regla | Valor |
| --- | --- |
| Intentos máximos | `NOTIFY_MAX_ATTEMPTS` = **3** |
| Espera entre intentos | exponencial: `NOTIFY_BASE_DELAY × 2^(intento−1)` → 1 s, 2 s, 4 s… (por defecto 1 s) |
| Fallo temporal | vuelve a `pending` con la espera; al llegar al máximo → DLQ |
| Fallo permanente | directo a la DLQ, sin reintentos |
| Recuperación | si el worker se cae con un mensaje en `processing`, otro intento lo reclama cuando vence el lease (`NOTIFY_LEASE_SECONDS` = 30 s) |
| Duplicados | `notifications.message_id` es **único** y se inserta con `INSERT OR IGNORE`: procesar dos veces no duplica el efecto |

### Simulación reproducible de fallos

| Correo del cliente | Comportamiento |
| --- | --- |
| `ana@example.com` | se entrega al primer intento |
| `ana+temporal2@example.com` | falla temporalmente 2 veces y luego se entrega |
| `ana+siempre@example.com` | falla temporalmente siempre → DLQ tras 3 intentos |
| `ana+permanente@example.com` | falla permanente → DLQ al primer intento |

## Endpoints nuevos

| Método | Ruta | Uso |
| --- | --- | --- |
| GET | `/notifications/queue` | ver la cola y el estado de cada mensaje |
| GET | `/notifications/dlq` | ver la DLQ |
| POST | `/notifications/dlq/{id}/reprocess` | **reprocesamiento explícito**: vuelve a `pending` con intentos en 0 |
| POST | `/notifications/process` | procesar ahora lo vencido (útil para demostrar sin esperar al hilo) |

## Garantías y alcance

- **Al menos una vez, no exactamente una vez.** Si el envío externo ocurre pero el proceso cae antes de marcar `done`, el mensaje se volverá a enviar al vencer el lease. Lo que sí se garantiza es que el **efecto local** (la fila en `notifications`) no se duplica.
- **Un solo worker.** El reclamo usa `BEGIN IMMEDIATE`, así que varios workers sobre el mismo archivo no tomarían el mismo mensaje a la vez, pero solo se probó y se documenta con un worker.
- Los pendientes sobreviven al reinicio porque están en SQLite.
- Bases antiguas: al iniciar se agrega la columna `notifications.message_id` si falta.

## Pruebas (`tests/test_notification_queue.py`)

| Criterio | Prueba |
| --- | --- |
| Confirmar deja pendiente y el worker lo procesa aparte | `test_confirm_leaves_a_pending_message_and_worker_delivers_it` |
| Reintentos con espera y máximo | `test_temporary_failure_is_retried_with_backoff_then_succeeds` |
| Al agotar → DLQ con contenido, intentos y causa | `test_exhausted_retries_go_to_dlq_with_content_attempts_and_cause` |
| Fallo permanente | `test_permanent_failure_goes_straight_to_dlq` |
| Consultar DLQ y reprocesar | `test_dlq_message_can_be_reprocessed_explicitly` |
| Sobrevive reinicio | `test_pending_messages_survive_restart` |
| Recuperación de un mensaje en procesamiento | `test_message_stuck_in_processing_is_recovered_after_lease` |
| Duplicado no duplica el efecto | `test_processing_a_duplicate_does_not_duplicate_the_effect` |
| Hilo real en segundo plano | `test_background_worker_thread_delivers` |

Las pruebas usan un reloj falso (`FakeClock`) para avanzar el tiempo sin esperar.
Dos pruebas existentes que verificaban la notificación inmediatamente después de confirmar se adaptaron para procesar la cola primero: es un **cambio de comportamiento intencional** de este adicional, no una regresión.

Resultado: **111 pruebas, 99,78 % de cobertura**.

## Evidencia

- Demostración con la API real (cola pendiente, 3 rondas del worker con espera, DLQ, entregas sin duplicados, reprocesamiento): [`capturas/adicional_2_demo_api.png`](capturas/adicional_2_demo_api.png) · texto: [`evidencias/adicional_2_demo_api.txt`](evidencias/adicional_2_demo_api.txt)
- Pruebas y cobertura: [`capturas/adicional_2_pytest_cobertura.png`](capturas/adicional_2_pytest_cobertura.png)

Para reproducir la demostración sin el worker en segundo plano:

```bash
git checkout adicional-2-cola-dlq
LAB_WORKER=0 NOTIFY_BASE_DELAY=1 uv run main.py
# crear clientes con correos ana@…, luis+temporal2@…, eva+siempre@…, confirmar pedidos y llamar a POST /notifications/process
```
