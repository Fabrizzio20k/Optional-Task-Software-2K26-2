# Bitácora de la tarea — FEKA STORE (Optional Task Software 2K26-2)

Este archivo registra, paso por paso, qué se hizo, por qué y con qué comando, para no perder el hilo.
El informe final (smells, técnicas, antes/después) está en [`INFORME.md`](INFORME.md).

> Entorno usado para ejecutar: Linux (VM de Cowork) con **Python 3.13.16** y **uv 0.13.4**.
> El entorno virtual se creó fuera del repositorio (`UV_PROJECT_ENVIRONMENT=$HOME/venv-feka`) para no ensuciar la carpeta.
> En Windows basta con `uv sync --locked` (crea `.venv`, que está en `.gitignore`).

---

## Paso 0 — Leer el enunciado y el código

- Leí `README.md`: 6 partes obligatorias (ejecutar, pruebas ≥ 80 %, SonarQube, ≥ 4 smells, ≥ 3 refactorizaciones, diagramas) y 3 adicionales opcionales.
- Leí todo el código (`main.py`, `app/database.py`, `app/products.py`, `app/customers.py`, `app/orders.py`) y la prueba inicial.
- Observación: casi toda la lógica está en `app/orders.py` → `OrderService`. Ahí están la mayoría de los smells.
- Git mostraba todos los archivos como modificados: era solo el fin de línea (CRLF de Windows). Se resolvió con `git config core.autocrlf true` (configuración local del repo); no hay cambios reales.

## Paso 1 — Ejecutar la aplicación (recorrido inicial)

Comandos:

```bash
uv sync --locked
uv run main.py            # en la VM se usó: uvicorn main:app --host 127.0.0.1 --port 8000
```

Se reprodujeron los 7 pasos del README con `curl` contra la API real (no con TestClient).
Resultado completo: [`evidencias/recorrido_antes.txt`](evidencias/recorrido_antes.txt).

| Paso | Esperado | Obtenido |
| --- | --- | --- |
| Cotizar / crear pedido | subtotal 5000, descuento 500, envío 1000, total 5500 | ✅ igual |
| Confirmar | 200, `confirmed` | ✅ |
| Stock del producto | 3 | ✅ 3 |
| Notificaciones | 1 | ✅ 1 |
| Repetir confirmación | 409 sin descontar stock | ✅ 409 y stock sigue en 3 |
| Swagger `/docs` | disponible | ✅ HTTP 200 |

> **Pendiente para ti (Windows):** sacar capturas de Swagger haciendo este mismo recorrido (el enunciado pide capturas).

## Paso 2 — Pruebas hasta ≥ 80 % de cobertura (ANTES de refactorizar)

Punto de partida: 4 pruebas, **67 %** de cobertura.

Se agregaron pruebas separadas por tipo:

| Archivo | Tipo | Qué cubre |
| --- | --- | --- |
| `tests/conftest.py` | fixtures | Base SQLite temporal por prueba (`tmp_path`), helpers para crear datos |
| `tests/test_pricing_rules.py` | **unitarias** | Descuentos por categoría, truncamiento a centavos, envíos y su límite exacto (9999 / 10000), umbral sobre el subtotal *antes* del descuento, fórmula del total, 404 de cliente/producto, que cotizar no toca stock, validaciones de `OrderInput` (líneas vacías, cantidad 0 o > 10000, productos repetidos, envío inválido, campos extra, tipos estrictos) |
| `tests/test_products_api.py` | integración | CRUD, orden por id, 404, 422, 204 sin cuerpo, no borrar producto con pedidos (409) |
| `tests/test_customers_api.py` | integración | CRUD, categoría por defecto, correo único (409 al crear y al editar), 404, 422, no borrar cliente con pedidos |
| `tests/test_orders_api.py` | integración | Ciclo de vida completo: crear, cotizar sin reservar stock, listado resumen vs. detalle, precios congelados, `PUT` recalcula con precios y categoría actuales, cancelados/confirmados son finales, notificación, stock exacto, stock consumido por otro pedido, 404 en todas las rutas, persistencia tras reinicio, **503** cuando SQLite no está disponible, `LAB_DB_PATH`, rollback de transacciones |
| `tests/test_orders.py` | integración (original) | Se dejó **sin modificar** |

Resultado: **84 pruebas, 99,67 % de cobertura** (`main.py` línea 38 es `uvicorn.run(...)` dentro de `if __name__ == "__main__"`, no se ejecuta en pruebas; no se excluyó nada).
Salida: [`evidencias/pytest_antes.txt`](evidencias/pytest_antes.txt).

```bash
uv run pytest --cov --cov-report=term-missing --cov-report=xml --cov-report=html --cov-fail-under=80
```

## Paso 3 — Congelar el estado previo (commit + tag)

```bash
git add -A
git commit -m "test: ampliar pruebas unitarias y de integración (99% cobertura) antes de refactorizar"
git tag -a antes-refactor -m "Estado previo a la refactorización"
```

Commit `52ec14a`, tag **`antes-refactor`**. Esta es la *revisión inicial* que pide el enunciado (código original + pruebas).

## Paso 4 — SonarQube (no se pudo ejecutar aquí)

La VM donde se trabajó **no tiene Docker ni SonarScanner**, así que este paso queda para ti en Windows:

1. `docker compose up -d` → abrir http://localhost:9000 (admin/admin, cambiar contraseña).
2. Crear proyecto local `tienda-lab`, generar token.
3. `git checkout antes-refactor` → correr pytest (genera `coverage.xml`) → `sonar-scanner` → **capturas del análisis inicial**.
4. `git checkout main` → pytest → `sonar-scanner` → **capturas del análisis final**.
5. Anotar versión de SonarQube y perfil de calidad (Sonar way). Mirar **cobertura global**, no la de código nuevo.

## Paso 5 — Identificar code smells

Se leyeron los métodos de `OrderService` buscando repetición, tamaño, parámetros y condicionales. Resultado (detalle en `INFORME.md` §4):

1. **Duplicated Code** — cálculo de precio repetido en `save` (44–64) y `quote` (97–116); búsqueda + 404 repetida 4 veces.
2. **Long Method** — `save` (33–90), 58 líneas mezclando validación, reglas y SQL.
3. **Long Parameter List + Flag Argument** — `save(customer_id, address, city, postal_code, shipping, items, order_id=None)`.
4. **Switch Statements / números mágicos** — `if/elif` sobre categoría y envío con 10, 15, 10_000, 1_000, 2_500; estados como strings sueltos.

## Paso 6 — Refactorizar

Cambios (detalle con antes/después en `INFORME.md` §5):

1. **Extract Function / Move Function** → nuevo `app/order_pricing.py` con `discount_cents`, `shipping_cents`, `calculate_totals`; métodos privados `_price`, `_require_order`, `_require_pending`, `_detail`, `_insert_lines`, `_deduct_stock`, `_notify_confirmation`, `_set_status`.
2. **Replace Magic Number with Symbolic Constant + tabla de búsqueda** → `DISCOUNT_PERCENT_BY_CATEGORY`, `SHIPPING_COST_CENTS`, `FREE_STANDARD_SHIPPING_FROM_CENTS`, `PENDING/CONFIRMED/CANCELLED`.
3. **Introduce Parameter Object + Remove Flag Argument** → `save(...)` se dividió en `create(data)` y `replace(order_id, data)`; rutas usan `Depends(order_service)`.
4. (extra) **Introduce Value Object** → `OrderTotals` y `PricedOrder`.

Verificaciones después de refactorizar:

| Verificación | Resultado |
| --- | --- |
| Las 84 pruebas previas, **sin tocarlas** | ✅ pasan |
| Pruebas unitarias nuevas de `order_pricing.py` | ✅ 14 nuevas → 98 en total |
| Cobertura | ✅ 99,69 % ([`evidencias/pytest_despues.txt`](evidencias/pytest_despues.txt)) |
| `openapi.json` antes vs. después | ✅ idéntico (mismo contrato HTTP) |
| Recorrido del README con `curl` | ✅ idéntico ([`evidencias/recorrido_despues.txt`](evidencias/recorrido_despues.txt)) |

## Paso 7 — Diagramas de clases

- Fuente Mermaid: `docs/diagramas/clases_inicial.mmd` y `clases_final.mmd`.
- Exportados a SVG y PNG con `mmdc` (Mermaid CLI): `mmdc -i clases_final.mmd -o clases_final.svg -b white`.
- Solo clases reales; funciones y routers aparecen como notas.

## Paso 8 — Informe y entrega

- `docs/INFORME.md`: smells, técnicas, antes/después, comandos, limitaciones.
- Commit final sobre `main`. No se suben `.venv`, `data/`, `coverage.xml`, `htmlcov/` (ya están en `.gitignore`).

## Qué te falta a ti (checklist)

- [x] Capturas de Swagger con el recorrido (hecho, Paso 9).
- [x] Captura de pytest y de `htmlcov/index.html` antes y después (hecho, Paso 9).
- [ ] SonarQube antes (`antes-refactor`) y después (`main`) con capturas, versión y perfil de calidad → guardarlas en `docs/capturas/sonarqube_*`.
- [ ] `git push` (y `git push origin antes-refactor` para subir el tag).
- [ ] (Opcional) Adicionales 1, 2 o 3.

## Paso 9 — Capturas automáticas

Para no depender de capturas manuales se usó **Playwright** (navegador Chromium automatizado):

1. Se levantó la app con `uvicorn` en cada revisión (`antes-refactor` y `main`) con una base nueva.
2. En Swagger (`/docs`) se ejecutó el recorrido completo con **Try it out → Execute** y se capturó cada operación con su respuesta (201, 200 y el 409 final).
3. Se capturó la salida de `pytest --cov` y el reporte `htmlcov/index.html`.
4. Se agregó `radon` (complejidad ciclomática) como evidencia complementaria: `save` pasó de **C (15)** a métodos de complejidad **A (1)**.
5. Se capturó `git log` (tag `antes-refactor` y commit final) y la comparación del `openapi.json` (idéntico).

SonarQube no se pudo ejecutar aquí: la red del entorno bloquea Docker Hub y las descargas de SonarSource, y entrar a SonarQube exige tu contraseña. Queda como único pendiente obligatorio.

## Paso 11 — Adicional 2: cola y DLQ (rama `adicional-2-cola-dlq`)

- Confirmar encola un mensaje en `notification_queue` dentro de la misma transacción; un worker en un hilo lo procesa.
- Reintentos (máximo 3, espera exponencial), DLQ con causa, reprocesamiento explícito, lease para recuperar mensajes en `processing`, efecto único por `message_id`.
- Fallos simulados de forma reproducible con el correo del cliente (`+temporal2`, `+siempre`, `+permanente`).
- 14 pruebas nuevas con reloj falso. 111 pruebas, 99,78 %.
- Detalle: [`ADICIONAL_2_COLA_DLQ.md`](ADICIONAL_2_COLA_DLQ.md).
