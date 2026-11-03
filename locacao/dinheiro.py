from datetime import date
from decimal import ROUND_HALF_UP, Decimal


def centavos(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def comissao(valor: Decimal, participacao_pct: Decimal, percentual: Decimal) -> Decimal:
    return centavos(valor * participacao_pct / 100 * percentual / 100)


def fator_acumulado(variacoes_pct: list[Decimal]) -> Decimal:
    fator = Decimal(1)
    for v in variacoes_pct:
        fator *= 1 + v / 100
    return fator


def primeiro_dia(d: date) -> date:
    return d.replace(day=1)


def somar_meses(d: date, n: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 + n, 12)
    return date(y, m + 1, 1)
