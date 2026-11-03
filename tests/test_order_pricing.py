"""Pruebas unitarias puras de app/order_pricing.py (agregadas tras la refactorización).

Ya no necesitan base de datos: las reglas de precio quedaron aisladas.
"""
import pytest

from app.order_pricing import OrderTotals, calculate_totals, discount_cents, shipping_cents


@pytest.mark.parametrize("category, subtotal, expected", [
    ("regular", 5000, 0), ("premium", 5000, 500), ("business", 5000, 750),
    ("premium", 3333, 333), ("business", 3333, 499), ("business", 6, 0),
    ("desconocida", 5000, 0),  # comportamiento heredado: categoría no prevista => sin descuento
])
def test_discount_cents(category, subtotal, expected):
    assert discount_cents(category, subtotal) == expected


@pytest.mark.parametrize("shipping, subtotal, expected", [
    ("pickup", 1, 0), ("standard", 9_999, 1_000), ("standard", 10_000, 0),
    ("express", 9_999, 2_500), ("express", 10_000, 2_500),
])
def test_shipping_cents(shipping, subtotal, expected):
    assert shipping_cents(shipping, subtotal) == expected


def test_calculate_totals_applies_threshold_before_discount():
    totals = calculate_totals(10_000, "business", "standard")
    assert totals == OrderTotals(subtotal_cents=10_000, discount_cents=1_500, shipping_cents=0, total_cents=8_500)
    assert totals.as_dict() == {"subtotal_cents": 10_000, "discount_cents": 1_500,
                                "shipping_cents": 0, "total_cents": 8_500}


def test_order_totals_is_immutable():
    totals = calculate_totals(1_000, "regular", "pickup")
    with pytest.raises(AttributeError):
        totals.total_cents = 0
