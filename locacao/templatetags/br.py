from decimal import ROUND_HALF_UP, Decimal

from django import template

register = template.Library()

VAZIO = "—"
MESES = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")


@register.filter
def brl(valor):
    """Decimal("1234.5") -> "R$ 1.234,50"; negativos como "-R$ 10,00"."""
    if valor is None:
        return VAZIO
    v = Decimal(str(valor)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    texto = f"{abs(v):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{'-' if v < 0 else ''}R$ {texto}"


@register.filter
def data_br(d):
    return d.strftime("%d/%m/%Y") if d else VAZIO


NOMES_MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
               "novembro", "dezembro")


@register.filter
def nome_mes(n):
    """3 -> "março"."""
    return NOMES_MESES[n - 1] if n else VAZIO


@register.filter
def mes_br(d):
    return f"{MESES[d.month - 1]}/{d.year}" if d else VAZIO
