from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class OrderService:
    def __init__(self, database):
        self.database = database

    def save(self, customer_id, address, city, postal_code, shipping, items, order_id=None):
        with self.database.connect() as connection:
            if order_id is not None:
                old = connection.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
                if old is None:
                    raise HTTPException(404, "Pedido no encontrado")
                if old["status"] != "pending":
                    raise HTTPException(409, "Solo puede editar pedidos pendientes")
            customer = connection.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
            if customer is None:
                raise HTTPException(404, "Cliente no encontrado")
            subtotal = 0
            lines = []
            for item in items:
                product = connection.execute("SELECT * FROM products WHERE id = ?", (item.product_id,)).fetchone()
                if product is None:
                    raise HTTPException(404, f"Producto {item.product_id} no encontrado")
                subtotal += product["price_cents"] * item.quantity
                lines.append((item.product_id, item.quantity, product["price_cents"]))
            if customer["category"] == "premium":
                discount = subtotal * 10 // 100
            elif customer["category"] == "business":
                discount = subtotal * 15 // 100
            else:
                discount = 0
            if shipping == "pickup":
                delivery = 0
            elif shipping == "standard":
                delivery = 0 if subtotal >= 10_000 else 1_000
            else:
                delivery = 2_500
            total = subtotal - discount + delivery
            values = (customer_id, customer["category"], address, city, postal_code, shipping,
                      subtotal, discount, delivery, total)
            if order_id is None:
                cursor = connection.execute(
                    """INSERT INTO orders(customer_id, customer_category, address, city, postal_code,
                       shipping, subtotal_cents, discount_cents, shipping_cents, total_cents)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", values,
                )
                order_id = cursor.lastrowid
            else:
                connection.execute(
                    """UPDATE orders SET customer_id = ?, customer_category = ?, address = ?, city = ?,
                       postal_code = ?, shipping = ?, subtotal_cents = ?, discount_cents = ?,
                       shipping_cents = ?, total_cents = ? WHERE id = ?""", (*values, order_id),
                )
                connection.execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))
            connection.executemany(
                "INSERT INTO order_items(order_id, product_id, quantity, unit_price_cents) VALUES (?, ?, ?, ?)",
                [(order_id, *line) for line in lines],
            )
            result = dict(connection.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone())
            result["items"] = [dict(row) for row in connection.execute(
                "SELECT product_id, quantity, unit_price_cents FROM order_items WHERE order_id = ? ORDER BY id",
                (order_id,),
            )]
            return result

    def quote(self, data):
        with self.database.connect() as connection:
            customer = connection.execute("SELECT * FROM customers WHERE id = ?", (data.customer_id,)).fetchone()
            if customer is None:
                raise HTTPException(404, "Cliente no encontrado")
            subtotal = 0
            for item in data.items:
                product = connection.execute("SELECT * FROM products WHERE id = ?", (item.product_id,)).fetchone()
                if product is None:
                    raise HTTPException(404, f"Producto {item.product_id} no encontrado")
                subtotal += product["price_cents"] * item.quantity
            if customer["category"] == "premium":
                discount = subtotal * 10 // 100
            elif customer["category"] == "business":
                discount = subtotal * 15 // 100
            else:
                discount = 0
            if data.shipping == "pickup":
                delivery = 0
            elif data.shipping == "standard":
                delivery = 0 if subtotal >= 10_000 else 1_000
            else:
                delivery = 2_500
            return {"subtotal_cents": subtotal, "discount_cents": discount,
                    "shipping_cents": delivery, "total_cents": subtotal - discount + delivery}

    def get(self, order_id):
        with self.database.connect() as connection:
            order = connection.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if order is None:
                raise HTTPException(404, "Pedido no encontrado")
            result = dict(order)
            result["items"] = [dict(row) for row in connection.execute(
                "SELECT product_id, quantity, unit_price_cents FROM order_items WHERE order_id = ? ORDER BY id",
                (order_id,),
            )]
            return result

    def list(self):
        with self.database.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM orders ORDER BY id")]

    def confirm(self, order_id):
        with self.database.connect() as connection:
            order = connection.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if order is None:
                raise HTTPException(404, "Pedido no encontrado")
            if order["status"] != "pending":
                raise HTTPException(409, "El pedido ya no está pendiente")
            items = connection.execute("SELECT * FROM order_items WHERE order_id = ?", (order_id,)).fetchall()
            for item in items:
                cursor = connection.execute(
                    "UPDATE products SET stock = stock - ? WHERE id = ? AND stock >= ?",
                    (item["quantity"], item["product_id"], item["quantity"]),
                )
                if cursor.rowcount == 0:
                    raise HTTPException(409, f"Stock insuficiente para producto {item['product_id']}")
            customer = connection.execute("SELECT * FROM customers WHERE id = ?", (order["customer_id"],)).fetchone()
            message = f"Pedido {order_id} confirmado. Total: {order['total_cents']} centavos."
            connection.execute(
                "INSERT INTO notifications(order_id, recipient, message) VALUES (?, ?, ?)",
                (order_id, customer["email"], message),
            )
            connection.execute("UPDATE orders SET status = 'confirmed' WHERE id = ?", (order_id,))
            return {"id": order_id, "status": "confirmed", "message": message}

    def cancel(self, order_id):
        with self.database.connect() as connection:
            order = connection.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if order is None:
                raise HTTPException(404, "Pedido no encontrado")
            if order["status"] != "pending":
                raise HTTPException(409, "Solo puede cancelar pedidos pendientes")
            connection.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (order_id,))
            return {"id": order_id, "status": "cancelled"}

    def delete(self, order_id):
        with self.database.connect() as connection:
            order = connection.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if order is None:
                raise HTTPException(404, "Pedido no encontrado")
            if order["status"] != "pending":
                raise HTTPException(409, "Solo puede eliminar pedidos pendientes")
            connection.execute("DELETE FROM orders WHERE id = ?", (order_id,))

    def notifications(self, order_id):
        self.get(order_id)
        with self.database.connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM notifications WHERE order_id = ? ORDER BY id", (order_id,),
            )]


router = APIRouter(prefix="/orders", tags=["Orders"])


@router.post("/quote")
def quote_order(data: OrderInput, request: Request):
    return OrderService(request.app.state.database).quote(data)


@router.post("", status_code=201)
def create_order(data: OrderInput, request: Request):
    return OrderService(request.app.state.database).save(
        data.customer_id, data.address, data.city, data.postal_code, data.shipping, data.items,
    )


@router.get("")
def list_orders(request: Request):
    return OrderService(request.app.state.database).list()


@router.get("/{order_id}")
def get_order(order_id: int, request: Request):
    return OrderService(request.app.state.database).get(order_id)


@router.put("/{order_id}")
def update_order(order_id: int, data: OrderInput, request: Request):
    return OrderService(request.app.state.database).save(
        data.customer_id, data.address, data.city, data.postal_code, data.shipping, data.items, order_id,
    )


@router.delete("/{order_id}", status_code=204)
def delete_order(order_id: int, request: Request):
    OrderService(request.app.state.database).delete(order_id)
    return Response(status_code=204)


@router.post("/{order_id}/confirm")
def confirm_order(order_id: int, request: Request):
    return OrderService(request.app.state.database).confirm(order_id)


@router.post("/{order_id}/cancel")
def cancel_order(order_id: int, request: Request):
    return OrderService(request.app.state.database).cancel(order_id)


@router.get("/{order_id}/notifications")
def list_notifications(order_id: int, request: Request):
    return OrderService(request.app.state.database).notifications(order_id)
