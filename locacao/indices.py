import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.request import urlopen

from django.db import transaction

from .dinheiro import centavos, fator_acumulado, primeiro_dia, somar_meses
from .models import Cobranca, Config, Locacao, Reajuste, ValorAluguel

SERIES = {"IGPM": 189, "IPCA": 433}
URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{}/dados?formato=json&dataInicial={:%d/%m/%Y}&dataFinal={:%d/%m/%Y}"


class IndiceIndisponivel(Exception):
    pass


def buscar_variacoes(indice: str, inicio: date, fim: date) -> list[Decimal]:
    """Variações mensais (%) de inicio..fim, em ordem de data."""
    if indice not in SERIES:
        raise ValueError(f"índice sem série no BCB: {indice}")
    try:
        with urlopen(URL.format(SERIES[indice], inicio, fim), timeout=10) as resp:
            dados = json.load(resp)
        pontos = sorted((datetime.strptime(d["data"], "%d/%m/%Y").date(), Decimal(str(d["valor"]))) for d in dados)
    except (OSError, ValueError, KeyError, TypeError, InvalidOperation) as e:  # rede/HTTP/timeout, JSON ou formato
        raise IndiceIndisponivel(f"{indice}: {e}") from e
    variacoes = [v for d, v in pontos if inicio <= d <= fim]
    if len(variacoes) < 12:
        raise IndiceIndisponivel(f"{indice}: só {len(variacoes)} meses disponíveis")
    return variacoes


def janela(mes_base: int, ano: int) -> tuple[date, date]:
    fim = somar_meses(date(ano, mes_base, 1), -1)
    return somar_meses(fim, -11), fim


def calcular_reajuste(locacao, ano: int, percentual_manual: Decimal | None = None, buscar=buscar_variacoes) -> Reajuste:
    vigente_desde = date(ano, locacao.mes_base_reajuste, 1)
    r = Reajuste.objects.filter(locacao=locacao, ano=ano).first()
    if r and r.aplicado_em:
        raise ValueError("reajuste já aplicado")
    if percentual_manual is None:
        fator = fator_acumulado(buscar(locacao.indice, *janela(locacao.mes_base_reajuste, ano)))
        percentual = (fator - 1) * 100
    else:
        percentual = Decimal(str(percentual_manual))
    percentual = percentual.quantize(Decimal("0.0001"))
    valor_atual = locacao.valor_em(vigente_desde)
    r, _ = Reajuste.objects.update_or_create(locacao=locacao, ano=ano, defaults=dict(
        indice=locacao.indice, vigente_desde=vigente_desde, percentual=percentual, valor_atual=valor_atual,
        valor_calculado=centavos(valor_atual * (1 + percentual / 100)),
    ))
    return r


def marcar_enviado(r: Reajuste, data: date) -> None:
    r.enviado_em = data
    r.save(update_fields=["enviado_em"])


def decidir(r: Reajuste, aprovado: bool, data: date, valor_aprovado: Decimal | None = None) -> None:
    if aprovado:
        r.decisao, r.valor_aprovado = "APROVADO", valor_aprovado if valor_aprovado is not None else r.valor_calculado
    else:
        r.decisao, r.valor_aprovado = "RECUSADO", None
    r.decidido_em = data
    r.save(update_fields=["decisao", "valor_aprovado", "decidido_em"])


@transaction.atomic
def aplicar(r: Reajuste, data: date) -> None:
    if r.decisao != "APROVADO" or r.aplicado_em:
        raise ValueError("só se aplica reajuste aprovado e ainda não aplicado")
    loc = r.locacao
    if loc.valores.filter(vigente_desde=r.vigente_desde).exists():
        raise ValueError(f"Já existe um valor do aluguel em {r.vigente_desde:%d/%m/%Y}; exclua ou corrija esse valor "
                         "na locação antes de aplicar o reajuste")
    ValorAluguel.objects.create(locacao=loc, vigente_desde=r.vigente_desde, valor=r.valor_aprovado, motivo="REAJUSTE")
    fim_isencao = somar_meses(primeiro_dia(loc.inicio), loc.isencao_meses)
    Cobranca.objects.filter(
        locacao=loc, competencia__gte=max(r.vigente_desde, fim_isencao), quitada=False, alocacoes__isnull=True,
    ).update(valor_esperado=r.valor_aprovado)
    r.aplicado_em = data
    r.save(update_fields=["aplicado_em"])


def reajustes_devidos(hoje: date) -> list[tuple[Locacao, int]]:
    """Ano corrente e o anterior (mês-base de dezembro não some em janeiro). No ano anterior, só mês-base a partir
    de `gerar_a_partir_de`: antes disso o reajuste era da planilha."""
    inicio_sistema = Config.get().gerar_a_partir_de or date.min
    devidos = []
    qs = Locacao.objects.filter(ativa=True, mes_base_reajuste__isnull=False).exclude(indice="NENHUM").order_by("pk")
    for ano in (hoje.year - 1, hoje.year):
        escalonadas = ValorAluguel.objects.filter(motivo="ESCALONAMENTO", vigente_desde__year=ano).values("locacao")
        for loc in qs.exclude(reajustes__ano=ano).exclude(pk__in=escalonadas):
            base = date(ano, loc.mes_base_reajuste, 1)
            if ano < hoje.year and base < inicio_sistema:
                continue
            if hoje >= base and somar_meses(primeiro_dia(loc.inicio), 12) <= base:
                devidos.append((loc, ano))
    return devidos
