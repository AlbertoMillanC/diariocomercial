from decimal import Decimal, ROUND_HALF_UP

from django import template

register = template.Library()


@register.filter
def pesos(value):
    if value is None or value == "":
        value = 0
    n = Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"$ {int(n):,}".replace(",", ".")
