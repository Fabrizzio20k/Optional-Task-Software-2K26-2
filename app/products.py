import sqlite3

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field


class ProductInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    price_cents: int = Field(gt=0, le=100_000_000, strict=True)
    stock: int = Field(ge=0, le=1_000_000, strict=True)


router = APIRouter(prefix="/products", tags=["Products"])


@router.post("", status_code=201)
def create_product(data: ProductInput, request: Request):
    with request.app.state.database.connect() as connection:
        cursor = connection.execute(
            "INSERT INTO products(name, price_cents, stock) VALUES (?, ?, ?)",
            (data.name, data.price_cents, data.stock),
        )
        return dict(connection.execute("SELECT * FROM products WHERE id = ?", (cursor.lastrowid,)).fetchone())


@router.get("")
def list_products(request: Request):
    with request.app.state.database.connect() as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM products ORDER BY id")]


@router.get("/{product_id}")
def get_product(product_id: int, request: Request):
    with request.app.state.database.connect() as connection:
        row = connection.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Producto no encontrado")
        return dict(row)


@router.put("/{product_id}")
def update_product(product_id: int, data: ProductInput, request: Request):
    with request.app.state.database.connect() as connection:
        cursor = connection.execute(
            "UPDATE products SET name = ?, price_cents = ?, stock = ? WHERE id = ?",
            (data.name, data.price_cents, data.stock, product_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(404, "Producto no encontrado")
        return dict(connection.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone())


@router.delete("/{product_id}", status_code=204)
def delete_product(product_id: int, request: Request):
    try:
        with request.app.state.database.connect() as connection:
            cursor = connection.execute("DELETE FROM products WHERE id = ?", (product_id,))
            if cursor.rowcount == 0:
                raise HTTPException(404, "Producto no encontrado")
    except sqlite3.IntegrityError:
        raise HTTPException(409, "El producto pertenece a un pedido") from None
    return Response(status_code=204)
