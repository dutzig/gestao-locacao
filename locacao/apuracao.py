from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import Prefetch

from .cobrancas import aguarda_proprietario, saldo, situacao
from .dinheiro import comissao, primeiro_dia, somar_meses
from .models import Alocacao, Cobranca, Config, Pagamento

ZERO = Decimal("0")


@dataclass
class Linha:
    cobranca: Cobranca
    situacao: str
    recebido: Decimal
    saldo: Decimal


@dataclass
class PagamentoLinha:
    pagamento: Pagamento
    competencias: list[date]


@dataclass
class Resumo:
    mes: date
    esperada: Decimal
    realizada: Decimal
    pendente_atrasado: Decimal
    pendente_aguardando: Decimal
    comissao: Decimal
    regra_nova: bool
    pendentes: list[Linha]
    recebidos: list[Linha]
    pagamentos: list[PagamentoLinha]

    @property
    def pendente_total(self) -> Decimal:
        return self.pendente_atrasado + self.pendente_aguardando


def pela_data(data_pagamento: date | None, corte: date | None) -> bool:
    """O pagamento conta no mês em que entrou (regra nova)? Senão, no mês da competência."""
    return corte is not None and data_pagamento is not None and data_pagamento >= corte


def mes_da_comissao(competencia: date, data_pagamento: date | None, corte: date | None) -> date:
    """Mês em que um valor alocado conta na receita realizada e na comissão."""
    return primeiro_dia(data_pagamento) if pela_data(data_pagamento, corte) else competencia


def resumo_mes(mes: date, hoje: date, config: Config | None = None) -> Resumo:
    mes = primeiro_dia(mes)
    config = config or Config.get()
    pct = config.percentual_comissao
    regra_nova = config.mes_corte is not None and mes >= config.mes_corte

    cobrancas = (
        Cobranca.objects.filter(competencia=mes)
        .select_related("locacao__imovel__empresa", "locacao__inquilino")
        .prefetch_related(Prefetch("alocacoes", Alocacao.objects.select_related("pagamento")))
        .order_by("vencimento", "locacao__imovel__codigo")
    )
    linhas = [Linha(c, situacao(c, hoje), c.recebido, saldo(c)) for c in cobrancas]
    pendentes = [l for l in linhas if l.saldo > 0]
    aguardando = sum((l.saldo for l in pendentes if aguarda_proprietario(l.cobranca, l.situacao, hoje)), ZERO)
    recebidos = [l for l in linhas if l.recebido > 0 or l.situacao in ("PAGA", "ISENTA")]
    r = Resumo(
        mes=mes, esperada=sum((l.cobranca.valor_esperado for l in linhas), ZERO), realizada=ZERO,
        pendente_atrasado=sum((l.saldo for l in pendentes), ZERO) - aguardando, pendente_aguardando=aguardando,
        comissao=ZERO, regra_nova=regra_nova, pendentes=pendentes, recebidos=recebidos, pagamentos=[],
    )

    # Cada alocação conta uma vez: pagamento com data a partir do corte, no mês da data (regra nova);
    # sem data ou antes do corte, no mês da competência (regra antiga), mesmo que a competência seja depois do corte.
    corte = config.mes_corte
    for l in linhas:
        c = l.cobranca
        recebido = sum((a.valor for a in c.alocacoes.all() if not pela_data(a.pagamento.data, corte)), ZERO)
        r.realizada += recebido
        r.comissao += (
            c.comissao_importada if c.comissao_importada is not None and not regra_nova
            else comissao(recebido, c.locacao.imovel.participacao_pct, pct)
        )
    if regra_nova:
        pagamentos = (
            Pagamento.objects.filter(data__gte=mes, data__lt=somar_meses(mes, 1))
            .select_related("locacao__imovel")
            .prefetch_related("alocacoes__cobranca")
            .order_by("data", "pk")
        )
        for p in pagamentos:
            r.realizada += p.valor
            r.comissao += comissao(p.valor, p.locacao.imovel.participacao_pct, pct)
            r.pagamentos.append(PagamentoLinha(p, sorted({a.cobranca.competencia for a in p.alocacoes.all()})))
    return r


def resumo_periodo(inicio: date, fim: date, hoje: date) -> list[Resumo]:
    config = Config.get()
    mes, fim = primeiro_dia(inicio), primeiro_dia(fim)
    resumos = []
    while mes <= fim:
        resumos.append(resumo_mes(mes, hoje, config))
        mes = somar_meses(mes, 1)
    return resumos


def totais(resumos: list[Resumo]) -> dict[str, Decimal]:
    chaves = ("esperada", "realizada", "pendente_total", "comissao")
    return {k: sum((getattr(r, k) for r in resumos), ZERO) for k in chaves}
