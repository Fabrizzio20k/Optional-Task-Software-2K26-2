# Informe — Pruebas, análisis estático y refactorización (FEKA STORE)

| Dato | Valor |
| --- | --- |
| Revisión inicial (antes de refactorizar) | tag **`antes-refactor`** → commit `52ec14a` (código original + pruebas ampliadas) |
| Revisión final | último commit de `main` (ver `git log`) |
| Python / uv | 3.13.16 / 0.13.4 |
| Pruebas | 84 antes → **98 después** (las 84 originales sin cambios + 14 unitarias nuevas) |
| Cobertura de líneas (todo el código de aplicación) | **99,67 % antes** → **99,69 % después** (meta ≥ 80 %) |
| Contrato HTTP | `openapi.json` generado antes y después: **idéntico** |
| Recorrido del README | mismo resultado antes y después ([antes](evidencias/recorrido_antes.txt), [después](evidencias/recorrido_despues.txt)) |

La bitácora paso a paso está en [`PROCESO.md`](PROCESO.md).

---

## 1. Ejecución

Ver [`evidencias/recorrido_antes.txt`](evidencias/recorrido_antes.txt): subtotal 5000, descuento 500, envío 1000, total 5500; stock baja de 5 a 3; 1 notificación; segunda confirmación → 409 sin tocar stock.

## 2. Pruebas

| Tipo | Archivos | Ejemplos de lo que se comprueba |
| --- | --- | --- |
| Unitarias (reglas de negocio) | `test_pricing_rules.py`, `test_order_pricing.py` | descuento 0/10/15 %, truncamiento (3333 → 333 y 499), envío 9999 → 1000 y 10000 → 0, umbral sobre el subtotal **antes** del descuento, express siempre 2500, validaciones de `OrderInput` |
| Integración API + SQLite | `test_products_api.py`, `test_customers_api.py`, `test_orders_api.py`, `test_orders.py` | CRUD, 404/409/422/503, `PUT` recalcula, pedidos congelan precios, estados finales, stock insuficiente con rollback total, persistencia tras reiniciar, `LAB_DB_PATH` |

Cada prueba usa una base temporal propia (`tmp_path`). Única línea sin cubrir: `main.py:38` (`uvicorn.run` dentro de `if __name__ == "__main__"`). No se excluyó código.

## 3. SonarQube y análisis estático

El análisis de **SonarQube debe ejecutarse en tu máquina** (Docker + SonarScanner): en el entorno usado no se pudo descargar la imagen `sonarqube:community` ni el servidor (red restringida), y el inicio de sesión de SonarQube requiere tu contraseña. El proyecto ya trae `sonar-project.properties` con clave `tienda-lab` (pasos en §7).

Como **evidencia complementaria** (no reemplaza a SonarQube) se midió la complejidad ciclomática con `radon`:

| Método | Antes | Después |
| --- | --- | --- |
| `OrderService.save` → `create` / `replace` | **C (15)** | A (1) / A (1) |
| `OrderService.quote` | B (9) | A (1) |
| Paso más complejo del servicio | `save` C (15) | `_price` A (5) |

Capturas: [`capturas/complejidad_radon_antes.png`](capturas/complejidad_radon_antes.png), [`capturas/complejidad_radon_despues.png`](capturas/complejidad_radon_despues.png).

## Capturas de evidencia

Todas en [`docs/capturas/`](capturas/), tomadas automáticamente con Playwright sobre la app real (uvicorn) en cada revisión:

| Evidencia | Antes (`antes-refactor`) | Después (`main`) |
| --- | --- | --- |
| Swagger — vista general | `swagger_00_antes_overview.png` | `swagger_00_despues_overview.png` |
| Recorrido en Swagger (8 pasos: cliente, producto, cotizar, crear, confirmar, stock = 3, notificación, 409) | `swagger_01…08_*_antes.png` | `swagger_01…08_*_despues.png` |
| pytest + cobertura en terminal | `pytest_cobertura_antes.png` | `pytest_cobertura_despues.png` |
| Reporte HTML de cobertura | `htmlcov_antes.png` | `htmlcov_despues.png` |
| Complejidad (radon) | `complejidad_radon_antes.png` | `complejidad_radon_despues.png` |
| Revisiones git y contrato OpenAPI idéntico | `git_revisiones_y_contrato.png` | |

---

## 4. Code smells identificados (código inicial, tag `antes-refactor`)

### Smell 1 — Duplicated Code (categoría: *Dispensables*)

- **Dónde:** `app/orders.py`, `OrderService.save` líneas **44–64** y `OrderService.quote` líneas **97–116** (cálculo de subtotal, descuento, envío y total). También la búsqueda + 404 del pedido en `get` 120–122, `confirm` 136–138, `cancel` 160–162, `delete` 170–172, y la consulta de líneas en 86–89 y 124–127.
- **Fragmento:**

```python
# save()  (líneas 52-63)                      # quote()  (líneas 103-114)
if customer["category"] == "premium":           if customer["category"] == "premium":
    discount = subtotal * 10 // 100                 discount = subtotal * 10 // 100
elif customer["category"] == "business":        elif customer["category"] == "business":
    discount = subtotal * 15 // 100                 discount = subtotal * 15 // 100
...                                             ...
```

- **Problema de mantenimiento:** la regla de precio vive en dos lugares. **Cambio que lo hace evidente:** “el envío express pasa a 3000” o “premium sube a 12 %”: hay que editar `save` y `quote` por igual; si se olvida uno, la **cotización** deja de coincidir con el **pedido creado** (exactamente el tipo de regresión que la prueba `test_quote_matches_created_order_and_does_not_reserve_stock` detecta).

### Smell 2 — Long Method (categoría: *Bloaters*)

- **Dónde:** `app/orders.py`, `OrderService.save`, líneas **33–90** (58 líneas).
- **Fragmento:** en un solo método: valida si el pedido existe y está pendiente (36–40), busca el cliente (41–43), valoriza cada producto (44–51), calcula descuento (52–57), envío (58–63), total (64), arma la tupla (65–66), hace `INSERT` **o** `UPDATE` (67–80), inserta líneas (81–84) y relee el resultado (85–90).
- **Problema:** mezcla validación, reglas de negocio y persistencia; no se puede probar la regla de precio sin una base de datos. **Cambio evidente:** “agregar un cupón de descuento” obliga a meterse en el medio de un método de 58 líneas con dos ramas de SQL.

### Smell 3 — Long Parameter List + Flag Argument (categoría: *Bloaters*)

- **Dónde:** firma `save(self, customer_id, address, city, postal_code, shipping, items, order_id=None)` línea **33**; llamadas en `create_order` **195–197** y `update_order` **212–214**.
- **Fragmento:**

```python
return OrderService(request.app.state.database).save(
    data.customer_id, data.address, data.city, data.postal_code, data.shipping, data.items, order_id,
)
```

- **Problema:** se desarma un `OrderInput` que ya agrupa esos datos en 6 parámetros sueltos, y `order_id` actúa como **bandera** que cambia el comportamiento (crear vs. reemplazar). **Cambio evidente:** agregar un campo al pedido (p. ej. `notes`) obliga a tocar la firma y las dos llamadas; además `quote(data)` sí recibe el objeto, así que la interfaz es inconsistente.

### Smell 4 — Switch Statements / números mágicos (categoría: *Object-Orientation Abusers*; relacionado con *Primitive Obsession*)

- **Dónde:** cadenas `if/elif` sobre `customer["category"]` (52–57, 103–108) y sobre `shipping` (58–63, 109–114), con literales `10`, `15`, `10_000`, `1_000`, `2_500`. Los estados se manejan como strings sueltos `"pending"`, `'confirmed'`, `'cancelled'` (39, 139, 155, 163, 165, 173).
- **Problema:** los valores del negocio no tienen nombre y la decisión está repartida en condicionales. **Cambio evidente:** “agregar categoría `vip` con 20 %” o “nuevo envío `same_day`” implica buscar y modificar todos los `if/elif` (en dos métodos), en vez de agregar una entrada a una tabla.

### Smells adicionales observados (no refactorizados, ver limitaciones)

- **Inappropriate Intimacy / mezcla de capas:** `OrderService` lanza `HTTPException` (FastAPI) desde la lógica de negocio.
- **Duplicated Code menor** en `products.py` y `customers.py`: `SELECT * FROM … WHERE id = ?` + `HTTPException(404)` repetidos.

---

## 5. Refactorizaciones aplicadas

### Técnica 1 — Extract Function / Extract Method (+ Move Function)

- **Problema que resuelve:** Smell 1 (duplicado) y Smell 2 (método largo).
- **Dónde:** el cálculo de precio se extrajo a funciones puras en el nuevo módulo **`app/order_pricing.py`** (`discount_cents`, `shipping_cents`, `calculate_totals`). En `OrderService` se extrajeron pasos privados: `_price`, `_require_order`, `_require_pending`, `_insert_lines`, `_detail`, `_deduct_stock`, `_notify_confirmation`, `_set_status`.
- **Antes / después:**

```python
# ANTES: quote() repetía 20 líneas de save()
def quote(self, data):
    with self.database.connect() as connection:
        customer = ...; subtotal = 0
        for item in data.items: ...
        if customer["category"] == "premium": ...
        if data.shipping == "pickup": ...
        return {"subtotal_cents": subtotal, ...}

# DESPUÉS
def quote(self, data):
    with self.database.connect() as connection:
        return self._price(connection, data).totals.as_dict()
```

```python
# ANTES (repetido en get, confirm, cancel, delete)
order = connection.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
if order is None:
    raise HTTPException(404, "Pedido no encontrado")
if order["status"] != "pending":
    raise HTTPException(409, "Solo puede cancelar pedidos pendientes")

# DESPUÉS
self._require_pending(self._require_order(connection, order_id), "Solo puede cancelar pedidos pendientes")
```

- **Por qué mejora:** la regla de precio existe una sola vez y se prueba sin base de datos (`tests/test_order_pricing.py`); ningún método público supera ~15 líneas; cada paso tiene nombre. Las transacciones no cambian: todos los pasos reciben la misma `connection` dentro del mismo `with self.database.connect()`.

### Técnica 2 — Replace Magic Number with Symbolic Constant + Replace Conditional with Lookup Table

- **Problema que resuelve:** Smell 4.
- **Dónde:** `app/order_pricing.py` y constantes de estado en `app/orders.py`.
- **Antes / después:**

```python
# ANTES
if shipping == "pickup":
    delivery = 0
elif shipping == "standard":
    delivery = 0 if subtotal >= 10_000 else 1_000
else:
    delivery = 2_500

# DESPUÉS
SHIPPING_COST_CENTS = {"pickup": 0, "standard": 1_000, "express": 2_500}
FREE_STANDARD_SHIPPING_FROM_CENTS = 10_000

def shipping_cents(shipping, subtotal_cents):
    if shipping == "standard" and subtotal_cents >= FREE_STANDARD_SHIPPING_FROM_CENTS:
        return 0
    return SHIPPING_COST_CENTS[shipping]
```

```python
DISCOUNT_PERCENT_BY_CATEGORY = {"regular": 0, "premium": 10, "business": 15}
PENDING, CONFIRMED, CANCELLED = "pending", "confirmed", "cancelled"
```

- **Por qué mejora:** agregar una categoría o tipo de envío es agregar una entrada a un diccionario; los valores del negocio tienen nombre y se leen como el README. Se conservó el comportamiento heredado de que una categoría no prevista no tenga descuento (`.get(category, 0)`).

### Técnica 3 — Introduce Parameter Object + Remove Flag Argument (Split Method)

- **Problema que resuelve:** Smell 3.
- **Dónde:** `save(customer_id, address, city, postal_code, shipping, items, order_id=None)` se reemplazó por **`create(data)`** y **`replace(order_id, data)`**, que reciben el `OrderInput` completo.
- **Antes / después:**

```python
# ANTES
@router.put("/{order_id}")
def update_order(order_id: int, data: OrderInput, request: Request):
    return OrderService(request.app.state.database).save(
        data.customer_id, data.address, data.city, data.postal_code, data.shipping, data.items, order_id,
    )

# DESPUÉS
@router.put("/{order_id}")
def update_order(order_id: int, data: OrderInput, service: Service):
    return service.replace(order_id, data)
```

- **Por qué mejora:** cada método hace una sola cosa (crear o reemplazar) sin bandera; agregar un campo al pedido ya no cambia firmas. De paso, la construcción repetida `OrderService(request.app.state.database)` en las 9 rutas se reemplazó por una dependencia de FastAPI (`Depends(order_service)`), sin cambiar rutas ni respuestas.

### Técnica 4 (complementaria) — Introduce Value Object

- **Dónde:** `OrderTotals` (dataclass inmutable) y `PricedOrder` en lugar de tuplas/diccionarios sueltos con `subtotal`, `discount`, `delivery`, `total`.
- **Por qué mejora:** los cuatro importes viajan juntos, con nombres, y no se pueden modificar por accidente (`test_order_totals_is_immutable`).

### Qué NO cambió (verificado)

- Rutas, métodos, códigos de estado y cuerpos: `openapi.json` idéntico y las 84 pruebas previas pasan sin modificar sus expectativas.
- Persistencia y esquema SQLite: sin cambios en `app/database.py`.
- Integridad transaccional: confirmar sigue siendo todo-o-nada (`test_insufficient_stock_rolls_back_all_lines`, `test_second_order_fails_when_first_used_the_stock`).

---

## 6. Diagramas de clases

| | Fuente editable (Mermaid) | Vista exportada |
| --- | --- | --- |
| Inicial | [`diagramas/clases_inicial.mmd`](diagramas/clases_inicial.mmd) | [SVG](diagramas/clases_inicial.svg) · [PNG](diagramas/clases_inicial.png) |
| Final | [`diagramas/clases_final.mmd`](diagramas/clases_final.mmd) | [SVG](diagramas/clases_final.svg) · [PNG](diagramas/clases_final.png) |

Solo se modelan clases reales (modelos Pydantic, `Database`, `OrderService`, `OrderTotals`, `PricedOrder`, `HTTPException` externa). Las funciones de `order_pricing.py` y los routers se mencionan en notas, no como clases ficticias.

---

## 7. Comandos para reproducir

```bash
uv sync --locked
uv run pytest --cov --cov-report=term-missing --cov-report=xml --cov-report=html --cov-fail-under=80

# estado previo a la refactorización
git checkout antes-refactor && uv run pytest --cov --cov-fail-under=80 && git checkout main

# SonarQube (requiere Docker y SonarScanner CLI)
docker compose up -d
# crear proyecto "tienda-lab" en http://localhost:9000, generar token y luego:
sonar-scanner      # con SONAR_HOST_URL y SONAR_TOKEN en variables de entorno (ver README)
```

## 8. Limitaciones pendientes

- `OrderService` sigue lanzando `HTTPException`: se podría introducir excepciones de dominio (`OrderNotFound`, `OrderNotPending`, `OutOfStock`) traducidas a HTTP en el router. No se hizo para no ampliar el alcance.
- `products.py` y `customers.py` mantienen SQL directo en las rutas con duplicación menor.
- El método `OrderService.list` sombrea el nombre `list` de Python (se mantuvo por compatibilidad con el original; SonarQube podría reportarlo).
- **Pendiente para entregar (requiere tu máquina):** análisis de SonarQube **antes** (`git checkout antes-refactor`) y **después** (`main`) con capturas del resumen, cobertura global y hallazgos, más versión de SonarQube y perfil de calidad.
