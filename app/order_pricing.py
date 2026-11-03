"""Reglas de precio de un pedido (sin acceso a base de datos ni HTTP).

Todos los importes están en centavos enteros.
"""
from dataclasses import asdict, dataclass

DISCOUNT_PERCENT_BY_CATEGORY = {"regular": 0, "premium": 10, "business": 15}
SHIPPING_COST_CENTS = {"pickup": 0, "standard": 1_000, "express": 2_500}
FREE_STANDARD_SHIPPING_FROM_CENTS = 10_000


@dataclass(frozen=True)
class OrderTotals:
    subtotal_cents: int
    discount_cents: int
    shipping_cents: int
    total_cents: int

    def as_dict(self):
        return asdict(self)


def discount_cents(category, subtotal_cents):
    """Descuento por categoría del cliente, truncado a centavos enteros."""
    return subtotal_cents * DISCOUNT_PERCENT_BY_CATEGORY.get(category, 0) // 100


def shipping_cents(shipping, subtotal_cents):
    """Costo de envío; el umbral de envío gratis usa el subtotal antes del descuento."""
    if shipping == "standard" and subtotal_cents >= FREE_STANDARD_SHIPPING_FROM_CENTS:
        return 0
    return SHIPPING_COST_CENTS[shipping]


def calculate_totals(subtotal_cents, category, shipping):
    discount = discount_cents(category, subtotal_cents)
    delivery = shipping_cents(shipping, subtotal_cents)
    return OrderTotals(subtotal_cents, discount, delivery, subtotal_cents - discount + delivery)
