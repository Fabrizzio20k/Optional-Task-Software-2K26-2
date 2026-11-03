"""Pruebas unitarias de las reglas de precio y de validación de entrada.

Se ejercita OrderService.quote directamente (sin HTTP) sobre una base temporal,
y los modelos Pydantic sin levantar la API.
"""
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.orders import ItemInput, OrderInput, OrderService
from tests.conftest import insert_customer, insert_product


def quote(db, category, items, shipping="standard"):
    customer_id = insert_customer(db, category=category)
    lines = []
    for price, quantity in items:
        lines.append(ItemInput(product_id=insert_product(db, price), quantity=quantity))
    data = OrderInput(customer_id=customer_id, address="Calle 1", city="Lima", postal_code="15001",
                      shipping=shipping, items=lines)
    return OrderService(db).quote(data)


@pytest.mark.parametrize("category, expected_discount", [
    ("regular", 0),
    ("premium", 500),    # 10 % de 5000
    ("business", 750),   # 15 % de 5000
])
def test_discount_depends_on_customer_category(database, category, expected_discount):
    result = quote(database, category, [(2500, 2)])
    assert result["subtotal_cents"] == 5000
    assert result["discount_cents"] == expected_discount


@pytest.mark.parametrize("category, subtotal, expected", [
    ("premium", 3333, 333),    # 333.3 se trunca
    ("business", 3333, 499),   # 499.95 se trunca, no se redondea
    ("premium", 9, 0),
    ("business", 1, 0),
])
def test_discount_is_truncated_to_whole_cents(database, category, subtotal, expected):
    assert quote(database, category, [(subtotal, 1)], shipping="pickup")["discount_cents"] == expected


@pytest.mark.parametrize("shipping, subtotal, expected", [
    ("pickup", 1000, 0),
    ("standard", 9999, 1000),
    ("standard", 10000, 0),     # límite exacto: gratis
    ("standard", 25000, 0),
    ("express", 1000, 2500),
    ("express", 50000, 2500),   # express nunca es gratis
])
def test_shipping_cost(database, shipping, subtotal, expected):
    assert quote(database, "regular", [(subtotal, 1)], shipping=shipping)["shipping_cents"] == expected


def test_free_shipping_threshold_uses_subtotal_before_discount(database):
    # 10000 - 15 % = 8500 después del descuento, pero el envío sigue siendo gratis
    result = quote(database, "business", [(5000, 2)])
    assert result == {"subtotal_cents": 10000, "discount_cents": 1500, "shipping_cents": 0, "total_cents": 8500}


def test_total_is_subtotal_minus_discount_plus_shipping(database):
    result = quote(database, "premium", [(1234, 3), (999, 2)], shipping="express")
    subtotal = 1234 * 3 + 999 * 2
    discount = subtotal * 10 // 100
    assert result == {"subtotal_cents": subtotal, "discount_cents": discount,
                      "shipping_cents": 2500, "total_cents": subtotal - discount + 2500}


def test_quote_fails_for_unknown_customer(database):
    product_id = insert_product(database, 1000)
    data = OrderInput(customer_id=99, address="a", city="b", postal_code="c",
                      items=[ItemInput(product_id=product_id, quantity=1)])
    with pytest.raises(HTTPException) as error:
        OrderService(database).quote(data)
    assert error.value.status_code == 404


def test_quote_fails_for_unknown_product(database):
    customer_id = insert_customer(database)
    data = OrderInput(customer_id=customer_id, address="a", city="b", postal_code="c",
                      items=[ItemInput(product_id=77, quantity=1)])
    with pytest.raises(HTTPException) as error:
        OrderService(database).quote(data)
    assert error.value.status_code == 404
    assert "77" in error.value.detail


def test_quote_does_not_modify_stock(database):
    customer_id = insert_customer(database)
    product_id = insert_product(database, 1000, stock=1)
    data = OrderInput(customer_id=customer_id, address="a", city="b", postal_code="c",
                      items=[ItemInput(product_id=product_id, quantity=5)])
    OrderService(database).quote(data)
    with database.connect() as connection:
        assert connection.execute("SELECT stock FROM products").fetchone()[0] == 1


def base_order(**changes):
    data = {"customer_id": 1, "address": "Calle", "city": "Lima", "postal_code": "15001",
            "items": [{"product_id": 1, "quantity": 1}]}
    data.update(changes)
    return data


def test_order_input_defaults_to_standard_and_strips_text():
    order = OrderInput(**base_order(address="  Calle 5  "))
    assert order.shipping == "standard"
    assert order.address == "Calle 5"


@pytest.mark.parametrize("changes", [
    {"items": []},
    {"items": [{"product_id": 1, "quantity": 0}]},
    {"items": [{"product_id": 1, "quantity": 10_001}]},
    {"items": [{"product_id": 1, "quantity": 1}, {"product_id": 1, "quantity": 2}]},
    {"shipping": "drone"},
    {"address": "   "},
    {"customer_id": "1"},
    {"coupon": "X"},
])
def test_order_input_rejects_invalid_data(changes):
    with pytest.raises(ValidationError):
        OrderInput(**base_order(**changes))
