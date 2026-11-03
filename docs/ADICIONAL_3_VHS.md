# Adicional 3 — ¿Conviene adoptar VHS (Vertical, Hexagonal y Screaming) en FEKA STORE?

Rama: **`adicional-3-analisis-vhs`** (solo análisis; no se implementa). Las referencias al código son a la revisión refactorizada de `main`.

## 1. Qué aporta cada enfoque y cómo se relacionan

| Enfoque | Idea central | Qué aporta |
| --- | --- | --- |
| **Vertical Slice Architecture** (Jimmy Bogard) | Organizar el código por **funcionalidad o caso de uso** (una “rebanada” atraviesa entrada, lógica y datos) en lugar de por capas técnicas. Se acopla mucho *dentro* de la rebanada y poco *entre* rebanadas. | Cambios localizados: agregar “cancelar pedido” toca una carpeta. Cada rebanada puede usar la solución técnica que necesite. Las abstracciones compartidas aparecen solo cuando la duplicación lo justifica. |
| **Hexagonal / Ports & Adapters** (Alistair Cockburn, 2005) | La aplicación es un núcleo que habla con el exterior solo a través de **puertos**; los **adaptadores** traducen a una tecnología concreta. Adaptadores *driving* (HTTP, CLI, pruebas) llaman al núcleo; adaptadores *driven* (base de datos, correo, pagos) son llamados por él. | Aislamiento de tecnología (cambiar SQLite o el proveedor de correo = nuevo adaptador), varios canales de entrada para el mismo caso de uso, y pruebas del núcleo con dobles en lugar de infraestructura real. |
| **Screaming Architecture** (Robert C. Martin, 2011) | La estructura de carpetas debe “gritar” **qué hace el sistema** (Pedidos, Clientes, Inventario), no qué framework usa (controllers, services, repositories). | Navegación y comprensión inmediatas; el dominio queda visible y el framework pasa a ser un detalle. |

**Cómo encajan (VHS):** *Screaming* decide los **nombres y el primer nivel** de carpetas (por negocio); *Vertical* decide la **unidad de organización** dentro de cada área (un caso de uso por rebanada); *Hexagonal* decide **cómo cada rebanada toca el exterior** (puertos para lo que puede cambiar de tecnología). Son complementarios: VSA y Screaming responden “dónde va el código” y Hexagonal responde “de qué depende”.

## 2. La arquitectura actual frente a VHS (con referencias al código)

### Lo que facilitaría adoptarla

- **Ya hay cortes verticales por recurso:** `app/products.py`, `app/customers.py` y `app/orders.py` agrupan modelo de entrada, rutas y comportamiento (el README lo llama “VSA básica”).
- **Hay un núcleo de dominio puro:** `app/order_pricing.py` (`calculate_totals`, `OrderTotals`) no depende de FastAPI ni de SQLite. Es exactamente lo que en hexagonal va dentro del hexágono.
- **Existe una raíz de composición:** `main.create_app()` arma la app e inyecta `Database`; ahí se conectarían adaptadores (`application.state.database`, `Depends(order_service)` en `orders.py`).
- **Las transacciones están centralizadas** en `Database.connect()` (`BEGIN IMMEDIATE` / `commit` / `rollback`), lo que permite envolverlas en un puerto de “unidad de trabajo” sin reescribir la lógica.

### Lo que la dificultaría

- **El servicio conoce HTTP:** `OrderService` lanza `HTTPException` (`_require_order`, `_require_pending`, `_price`, `_deduct_stock`). En hexagonal el núcleo no debería saber de códigos 404/409.
- **SQL incrustado en la lógica:** `OrderService` y las rutas de `products.py`/`customers.py` ejecutan SQL y devuelven `sqlite3.Row` convertidos a `dict`. No hay un puerto de persistencia: cambiar a PostgreSQL implicaría tocar cada caso de uso.
- **Un servicio por recurso, no por caso de uso:** `OrderService` reúne cotizar, crear, reemplazar, confirmar, cancelar, eliminar y listar. En VSA estricta serían rebanadas separadas.
- **Efectos externos acoplados a la transacción:** la notificación se registra dentro de `confirm` (o se encola, en el adicional 2) usando la misma `connection`; un proveedor real de correo o pagos necesitaría un puerto explícito.
- **Nombres técnicos en el primer nivel:** `app/database.py` está al mismo nivel que las funcionalidades; con más integraciones aparecerían `app/email.py`, `app/payments.py`… y la estructura dejaría de “gritar” el negocio.

## 3. Escenario concreto de crecimiento

> La tienda empieza a vender por **web y por WhatsApp**, cobra con una **pasarela de pagos** (p. ej. Niubiz o Stripe), envía **correos reales** (SendGrid) y sincroniza **stock con el ERP** del almacén cada noche. Se suman devoluciones y cupones.

Consecuencias en la estructura actual: `orders.py` crecería con pagos, cupones y devoluciones; la confirmación necesitaría llamar a la pasarela y al ERP; aparecería un segundo canal (bot de WhatsApp o importación por lotes) que hoy tendría que pasar por HTTP o duplicar lógica; y las pruebas de confirmar dependerían de servicios externos.

### Propuesta breve de organización

```text
tienda/
├── pedidos/                      ← Screaming: área de negocio
│   ├── cotizar_pedido/           ← Vertical: un caso de uso por carpeta
│   │   ├── endpoint.py           (adaptador driving HTTP)
│   │   └── handler.py            (caso de uso)
│   ├── crear_pedido/
│   ├── confirmar_pedido/
│   │   ├── endpoint.py
│   │   └── handler.py            usa los puertos PasarelaPagos, Notificador, Inventario
│   ├── cancelar_pedido/
│   ├── precios.py                (núcleo puro: hoy order_pricing.py)
│   └── errores.py                (PedidoNoEncontrado, PedidoNoPendiente, SinStock)
├── clientes/  …
├── productos/ …
├── puertos/                      ← Hexagonal, SOLO para lo que cambia de tecnología
│   ├── notificador.py            (interfaz)  →  adaptadores/sendgrid.py, adaptadores/registro_local.py
│   ├── pasarela_pagos.py         (interfaz)  →  adaptadores/niubiz.py, adaptadores/pagos_falsos.py
│   └── inventario_externo.py     (interfaz)  →  adaptadores/erp.py
├── compartido/database.py        (unidad de trabajo SQLite)
└── main.py                       (raíz de composición: elige adaptadores)
```

Los errores de dominio (`errores.py`) se traducirían a 404/409 en un único manejador de excepciones de FastAPI, igual que hoy `main.py` traduce `sqlite3.OperationalError` a 503.

## 4. Conclusión: adopción **parcial**

| Decisión | Por qué |
| --- | --- |
| **Sí** a Screaming + Vertical (carpetas por área y por caso de uso) | El proyecto ya está organizado por funcionalidad; dar un paso más (una carpeta por caso de uso) cuesta poco y escala bien cuando `orders.py` empiece a crecer con pagos, cupones y devoluciones. |
| **Sí** a puertos y adaptadores **solo en los bordes que cambian**: notificaciones, pagos, ERP | Ahí está el beneficio real del hexágono: cambiar de proveedor sin tocar casos de uso y probar `confirmar_pedido` con dobles. |
| **No** a un hexágono completo con repositorio por entidad para el CRUD de productos y clientes | Es SQL simple sobre SQLite, sin integraciones; envolverlo en interfaces agregaría archivos y mapeos sin beneficio hoy (el enunciado pide no introducir patrones que el dominio no necesite). |

**Hoy, con el alcance actual, mantendría la estructura** (más la refactorización ya hecha). Adoptaría la versión parcial **cuando aparezca la primera integración externa real** (pasarela de pagos o correo real), que es el momento en que los puertos empiezan a pagarse solos.

### Beneficios de la arquitectura vs. problemas que se resuelven con una refactorización simple

| Problema | ¿Requiere VHS? |
| --- | --- |
| Cálculo de precio duplicado, método largo, números mágicos | **No.** Ya se resolvió con Extract Function y constantes (ver `INFORME.md`). |
| `HTTPException` dentro del servicio | **No.** Basta con excepciones de dominio y un manejador en `main.py`. |
| `OrderService` con demasiadas operaciones | **Parcialmente.** Se puede dividir en clases por caso de uso sin cambiar de arquitectura; VSA lo formaliza. |
| Cambiar de proveedor de correo o de pagos sin tocar la lógica | **Sí.** Es el aporte propio de los puertos y adaptadores. |
| Varios canales de entrada (HTTP, WhatsApp, lotes) reutilizando el mismo caso de uso | **Sí.** Adaptadores *driving* sobre el mismo handler. |
| Que la estructura muestre el negocio a un equipo que crece | **Sí.** Aporte de Screaming + Vertical. |

**Costos a considerar:** más archivos y saltos de navegación, una curva de aprendizaje para el equipo, el riesgo de abstraer el framework “por si acaso” (advertido por los propios autores de Ports & Adapters) y la necesidad de reglas sobre qué código es compartido entre rebanadas.

## 5. Fuentes consultadas

- Jimmy Bogard — *Vertical Slice Architecture*: https://www.jimmybogard.com/vertical-slice-architecture/ (referenciado en el artículo de Fran Iglesias).
- Fran Iglesias — *TalkingBit: Vertical Slice Architecture*: https://franiglesias.github.io/vertical-slice-architecture/
- Fran Iglesias — *TalkingBit: Hexagonal Architecture Explained*: https://franiglesias.github.io/hexagonal-explained/
- Herberto Graça — *Ports & Adapters Architecture* (sobre la propuesta de Alistair Cockburn, 2005): https://herbertograca.com/2017/09/14/ports-adapters-architecture/
- Juan Manuel Garrido de Paz — *Hexagonal Architecture*: https://jmgarridopaz.github.io/content/hexagonalarchitecture
- Robert C. Martin — *Screaming Architecture* (2011): https://blog.cleancoder.com/uncle-bob/2011/09/30/Screaming-Architecture.html (enlazado desde el artículo de Milan Jovanović).
- Milan Jovanović — *Screaming Architecture*: https://www.milanjovanovic.tech/blog/screaming-architecture
- Milan Jovanović — *Vertical Slice Architecture*: https://milanjovanovic.tech/blog/vertical-slice-architecture
- Level Up Coding — *Leveraging Hexagonal Architectures with Vertical Slice Architecture and Feature Toggles*: https://levelup.gitconnected.com/leveraging-hexagonal-architectures-with-vertical-slice-architecture-and-feature-toggles-a308a48641b3
