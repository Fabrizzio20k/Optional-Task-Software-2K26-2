import sqlite3
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field


class CustomerInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr = Field(max_length=254)
    category: Literal["regular", "premium", "business"] = "regular"


router = APIRouter(prefix="/customers", tags=["Customers"])


@router.post("", status_code=201)
def create_customer(data: CustomerInput, request: Request):
    try:
        with request.app.state.database.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO customers(name, email, category) VALUES (?, ?, ?)",
                (data.name, str(data.email), data.category),
            )
            return dict(connection.execute("SELECT * FROM customers WHERE id = ?", (cursor.lastrowid,)).fetchone())
    except sqlite3.IntegrityError:
        raise HTTPException(409, "El correo ya está registrado") from None


@router.get("")
def list_customers(request: Request):
    with request.app.state.database.connect() as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM customers ORDER BY id")]


@router.get("/{customer_id}")
def get_customer(customer_id: int, request: Request):
    with request.app.state.database.connect() as connection:
        row = connection.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Cliente no encontrado")
        return dict(row)


@router.put("/{customer_id}")
def update_customer(customer_id: int, data: CustomerInput, request: Request):
    try:
        with request.app.state.database.connect() as connection:
            cursor = connection.execute(
                "UPDATE customers SET name = ?, email = ?, category = ? WHERE id = ?",
                (data.name, str(data.email), data.category, customer_id),
            )
            if cursor.rowcount == 0:
                raise HTTPException(404, "Cliente no encontrado")
            return dict(connection.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone())
    except sqlite3.IntegrityError:
        raise HTTPException(409, "El correo ya está registrado") from None


@router.delete("/{customer_id}", status_code=204)
def delete_customer(customer_id: int, request: Request):
    try:
        with request.app.state.database.connect() as connection:
            cursor = connection.execute("DELETE FROM customers WHERE id = ?", (customer_id,))
            if cursor.rowcount == 0:
                raise HTTPException(404, "Cliente no encontrado")
    except sqlite3.IntegrityError:
        raise HTTPException(409, "El cliente tiene pedidos") from None
    return Response(status_code=204)
