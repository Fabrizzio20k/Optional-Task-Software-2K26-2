from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.order_pricing import OrderTotals, calculate_totals


class ItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: int = Field(gt=0, strict=True)
    quantity: int = Field(gt=0, le=10_000, strict=True)


class OrderInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    customer_id: int = Field(gt=0, strict=True)
    address: str = Field(min_length=1, max_length=200)
    city: str = Field(min_length=1, max_length=100)
    postal_code: str = Field(min_length=1, max_length=20)
    shipping: Literal["pickup", "standard", "express"] = "standard"
    items: list[ItemInput] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_products(self):
        if len({item.product_id for item in self.items}) != len(self.items):
            raise ValueError("No repita productos; indique la cantidad en una sola línea")
        return self


PENDING = "pending"
CONFIRMED = "confirmed"
CANCELLED = "cancelled"

ORDER_ITEMS_QUERY = (
    "SELECT product_id, quantity, unit_price_cents FROM order_items WHERE order_id = ? ORDER BY id"
)


@dataclass(frozen=True)
class PricedOrder:
    """Resultado de valorizar un pedido con los precios y la categoría actuales."""
    customer_category: str
    lines: list[tuple[int, int, int]]
    totals: OrderTotals


class OrderService:
    def __init__(self, database):
        self.database = database

    # --- Operaciones públicas -------------------------------------------------

    def quote(self, data):
        with self.database.connect() as connection:
            return self._price(connection, data).totals.as_dict()

    def create(self, data):
        with self.database.connect() as connection:
            priced = self._price(connection, data)
            cursor = connection.execute(
                """INSERT INTO orders(customer_id, customer_category, address, city, postal_code,
                   shipping, subtotal_cents, discount_cents, shipping_cents, total_cents)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", self._order_values(data, priced),
            )
            self._insert_lines(connection, cursor.lastrowid, priced.lines)
            return self._detail(connection, cursor.lastrowid)

    def replace(self, order_id, data):
        with self.database.connect() as connection:
            self._require_pending(self._require_order(connection, order_id), "Solo puede editar pedidos pendientes")
            priced = self._price(connection, data)
            connection.execute(
                """UPDATE orders SET customer_id = ?, customer_category = ?, address = ?, city = ?,
                   postal_code = ?, shipping = ?, subtotal_cents = ?, discount_cents = ?,
                   shipping_cents = ?, total_cents = ? WHERE id = ?""", (*self._order_values(data, priced), order_id),
            )
            connection.execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))
            self._insert_lines(connection, order_id, priced.lines)
            return self._detail(connection, order_id)

    def get(self, order_id):
        with self.database.connect() as connection:
            self._require_order(connection, order_id)
            return self._detail(connection, order_id)

    def list(self):
        with self.database.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM orders ORDER BY id")]

    def confirm(self, order_id):
        with self.database.connect() as connection:
            order = self._require_pending(self._require_order(connection, order_id), "El pedido ya no está pendiente")
            self._deduct_stock(connection, order_id)
            message = self._notify_confirmation(connection, order)
            self._set_status(connection, order_id, CONFIRMED)
            return {"id": order_id, "status": CONFIRMED, "message": message}

    def cancel(self, order_id):
        with self.database.connect() as connection:
            self._require_pending(self._require_order(connection, order_id), "Solo puede cancelar pedidos pendientes")
            self._set_status(connection, order_id, CANCELLED)
            return {"id": order_id, "status": CANCELLED}

    def delete(self, order_id):
        with self.database.connect() as connection:
            self._require_pending(self._require_order(connection, order_id), "Solo puede eliminar pedidos pendientes")
            connection.execute("DELETE FROM orders WHERE id = ?", (order_id,))

    def notifications(self, order_id):
        with self.database.connect() as connection:
            self._require_order(connection, order_id)
            return [dict(row) for row in connection.execute(
                "SELECT * FROM notifications WHERE order_id = ? ORDER BY id", (order_id,),
            )]

    # --- Pasos internos -------------------------------------------------------

    @staticmethod
    def _require_order(connection, order_id):
        order = connection.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if order is None:
            raise HTTPException(404, "Pedido no encontrado")
        return order

    @staticmethod
    def _require_pending(order, conflict_message):
        if order["status"] != PENDING:
            raise HTTPException(409, conflict_message)
        return order

    @staticmethod
    def _price(connection, data):
        customer = connection.execute("SELECT * FROM customers WHERE id = ?", (data.customer_id,)).fetchone()
        if customer is None:
            raise HTTPException(404, "Cliente no encontrado")
        lines = []
        for item in data.items:
            product = connection.execute("SELECT * FROM products WHERE id = ?", (item.product_id,)).fetchone()
            if product is None:
                raise HTTPException(404, f"Producto {item.product_id} no encontrado")
            lines.append((item.product_id, item.quantity, product["price_cents"]))
        subtotal = sum(quantity * unit_price for _, quantity, unit_price in lines)
        totals = calculate_totals(subtotal, customer["category"], data.shipping)
        return PricedOrder(customer["category"], lines, totals)

    @staticmethod
    def _order_values(data, priced):
        totals = priced.totals
        return (data.customer_id, priced.customer_category, data.address, data.city, data.postal_code,
                data.shipping, totals.subtotal_cents, totals.discount_cents, totals.shipping_cents,
                totals.total_cents)

    @staticmethod
    def _insert_lines(connection, order_id, lines):
        connection.executemany(
            "INSERT INTO order_items(order_id, product_id, quantity, unit_price_cents) VALUES (?, ?, ?, ?)",
            [(order_id, *line) for line in lines],
        )

    @staticmethod
    def _detail(connection, order_id):
        result = dict(connection.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone())
        result["items"] = [dict(row) for row in connection.execute(ORDER_ITEMS_QUERY, (order_id,))]
        return result

    @staticmethod
    def _deduct_stock(connection, order_id):
        """Descuenta stock de todas las líneas; si alguna falla, la transacción completa se revierte."""
        items = connection.execute("SELECT * FROM order_items WHERE order_id = ?", (order_id,)).fetchall()
        for item in items:
            cursor = connection.execute(
                "UPDATE products SET stock = stock - ? WHERE id = ? AND stock >= ?",
                (item["quantity"], item["product_id"], item["quantity"]),
            )
            if cursor.rowcount == 0:
                raise HTTPException(409, f"Stock insuficiente para producto {item['product_id']}")

    @staticmethod
    def _notify_confirmation(connection, order):
        customer = connection.execute("SELECT * FROM customers WHERE id = ?", (order["customer_id"],)).fetchone()
        message = f"Pedido {order['id']} confirmado. Total: {order['total_cents']} centavos."
        connection.execute(
            "INSERT INTO notifications(order_id, recipient, message) VALUES (?, ?, ?)",
            (order["id"], customer["email"], message),
        )
        return message

    @staticmethod
    def _set_status(connection, order_id, status):
        connection.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))


def order_service(request: Request) -> OrderService:
    return OrderService(request.app.state.database)


Service = Annotated[OrderService, Depends(order_service)]
router = APIRouter(prefix="/orders", tags=["Orders"])


@router.post("/quote")
def quote_order(data: OrderInput, service: Service):
    return service.quote(data)


@router.post("", status_code=201)
def create_order(data: OrderInput, service: Service):
    return service.create(data)


@router.get("")
def list_orders(service: Service):
    return service.list()


@router.get("/{order_id}")
def get_order(order_id: int, service: Service):
    return service.get(order_id)


@router.put("/{order_id}")
def update_order(order_id: int, data: OrderInput, service: Service):
    return service.replace(order_id, data)


@router.delete("/{order_id}", status_code=204)
def delete_order(order_id: int, service: Service):
    service.delete(order_id)
    return Response(status_code=204)


@router.post("/{order_id}/confirm")
def confirm_order(order_id: int, service: Service):
    return service.confirm(order_id)


@router.post("/{order_id}/cancel")
def cancel_order(order_id: int, service: Service):
    return service.cancel(order_id)


@router.get("/{order_id}/notifications")
def list_notifications(order_id: int, service: Service):
    return service.notifications(order_id)
