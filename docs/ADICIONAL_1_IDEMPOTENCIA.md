# Adicional 1 — Idempotencia en `POST /orders`

Rama: **`adicional-1-idempotencia`** (parte de `main`; no forma parte de la implementación base).

## Contrato

| Elemento | Definición |
| --- | --- |
| Cabecera | `Idempotency-Key: <texto de 1 a 255 caracteres>` (opcional) |
| Sin cabecera | Comportamiento original: cada `POST` crea un pedido nuevo |
| Misma clave + mismo contenido | `201` con el **mismo cuerpo** de la respuesta original y cabecera `Idempotent-Replayed: true`; no se crea otro pedido |
| Misma clave + contenido distinto | `409` `"La clave de idempotencia ya se usó con otro contenido"` |
| Clave vacía o > 255 caracteres | `422` |
| Petición que falla (404, 422, error interno) | No se guarda la clave: se puede reintentar con la misma clave |

“Mismo contenido” = mismo `OrderInput` **ya validado y normalizado** (por ejemplo, los espacios recortados por Pydantic no cuentan como diferencia). Se compara el SHA-256 de su JSON canónico (`sort_keys`).

## Diseño

- Tabla nueva `idempotency_keys(key PRIMARY KEY, request_hash, order_id, response_json)` creada en `Database.initialize()` con `CREATE TABLE IF NOT EXISTS` (funciona sobre bases existentes).
- En `OrderService.create(data, idempotency_key)`, dentro de **una sola transacción** `BEGIN IMMEDIATE`:
  1. Se busca la clave. Si existe: mismo hash → se devuelve la respuesta guardada; hash distinto → 409.
  2. Si no existe: se valoriza, se inserta el pedido y sus líneas y se registra la clave con la respuesta.
  3. `commit` de todo junto. Si algo falla antes, `rollback` de todo: ni pedido parcial ni clave bloqueada.
- **Concurrencia:** `BEGIN IMMEDIATE` toma el bloqueo de escritura de SQLite al inicio, así que dos peticiones con la misma clave se serializan: la segunda ve la clave ya registrada. La `PRIMARY KEY` es una segunda barrera de unicidad.
- **Reinicio:** la clave vive en SQLite, por eso sobrevive al reinicio.

## Alcance y decisiones

- Solo aplica a `POST /orders` (no a `PUT`, `confirm`, etc.).
- La repetición devuelve la **foto original** de la respuesta, aunque luego el pedido se haya editado, confirmado o eliminado. `order_id` se guarda sin clave foránea para no impedir eliminar pedidos pendientes.
- Las claves no caducan (el enunciado no lo exige).
- Funciona para una sola instancia con un único archivo SQLite; con varias bases o réplicas haría falta un almacén de claves compartido.

## Pruebas (`tests/test_idempotency.py`)

| Criterio | Prueba |
| --- | --- |
| Misma clave y contenido → resultado original | `test_same_key_and_content_returns_original_without_new_order` |
| Misma clave, otro contenido → conflicto | `test_same_key_with_different_content_is_a_conflict` |
| Concurrencia → un solo pedido (8 hilos) | `test_concurrent_requests_with_same_key_create_a_single_order` |
| Sobrevive al reinicio | `test_key_survives_restart` |
| Un error no bloquea la clave | `test_failed_request_does_not_lock_the_key` |
| Sin pedido parcial ante un fallo a mitad | `test_error_after_inserting_order_leaves_no_partial_order_or_key` |
| Sin cabecera no cambia nada | `test_without_key_behaviour_is_unchanged` |

Resultado: **107 pruebas, 99,71 % de cobertura**.

```bash
git checkout adicional-1-idempotencia
uv run pytest --cov --cov-fail-under=80
curl -X POST localhost:8000/orders -H "Content-Type: application/json" -H "Idempotency-Key: pedido-123" -d @pedido.json
```

## Evidencia

- Demostración con la API real (crear, reintentar, conflicto, listado, sin cabecera): [`capturas/adicional_1_demo_api.png`](capturas/adicional_1_demo_api.png) · texto: [`evidencias/adicional_1_demo_api.txt`](evidencias/adicional_1_demo_api.txt)
- Pruebas y cobertura: [`capturas/adicional_1_pytest_cobertura.png`](capturas/adicional_1_pytest_cobertura.png)
