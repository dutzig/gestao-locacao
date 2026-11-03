from dataclasses import dataclass
from datetime import date, timedelta

from django.db.models import F, Q
from django.urls import reverse

from .apuracao import resumo_mes
from .cobrancas import aguarda_proprietario, gerar_cobrancas, saldo, situacao
from .dinheiro import primeiro_dia, somar_meses
from .indices import reajustes_devidos
from .models import Cobranca, ComissaoMes, Config, Despesa, Empresa, Imovel, Locacao, Reajuste, RevisaoImportacao, TarefaManual
from .templatetags.br import brl, data_br, mes_br


@dataclass
class Item:
    tipo: str
    titulo: str
    detalhe: str
    url: str
    prioridade: int  # 1 = mais urgente
    concluir: str = ""  # URL (POST) p/ concluir tarefa manual


def url_de(destino: str, pk: int | None = None) -> str:
    return reverse(destino, args=[] if pk is None else [pk])


EM_TRATAMENTO = ("JURIDICO", "ACORDO")  # dívida seguida à parte: sai da lista mês a mês


def vencidas(hoje):
    """Cobranças vencidas sem quitação de locações ativas e fora de tratamento de dívida:
    (cobranca, situacao, aguarda_proprietario)."""
    qs = (
        Cobranca.objects.filter(quitada=False, vencimento__lt=hoje, valor_esperado__gt=0, locacao__ativa=True)
        .exclude(locacao__situacao_divida__in=EM_TRATAMENTO)
        .select_related("locacao__imovel__empresa").prefetch_related("alocacoes")
        .order_by("vencimento", "locacao__imovel__codigo")
    )
    for c in qs:
        s = situacao(c, hoje)
        if s in ("ATRASADA", "AGUARDANDO", "PARCIAL"):
            yield c, s, aguarda_proprietario(c, s, hoje)


def dividas(hoje):
    """Cobranças com saldo de locações encerradas e as vencidas de ativas com situação de dívida, por locação."""
    qs = (
        Cobranca.objects.filter(quitada=False, valor_esperado__gt=0)
        .filter(Q(locacao__ativa=False) | (~Q(locacao__situacao_divida="NENHUMA") & Q(vencimento__lt=hoje)))
        .select_related("locacao__imovel", "locacao__inquilino").prefetch_related("alocacoes")
        .order_by("locacao__imovel__codigo", "locacao_id", "competencia")
    )
    return [c for c in qs if saldo(c) > 0]


def _dividas(hoje, config):
    resolvidas = ("QUITADA", "BAIXADA")
    n = len({c.locacao_id for c in dividas(hoje) if not c.locacao.ativa and c.locacao.situacao_divida not in resolvidas})
    if n:
        yield Item("DIVIDA", f"Dívidas de locações encerradas ({n})", "", url_de("dividas"), 2)


def _cobrancas(hoje, config):
    aguardando = []
    for c, s, aguarda in vencidas(hoje):
        codigo = c.locacao.imovel.codigo
        parcial = f"pago parcial, falta {brl(saldo(c))}" if s == "PARCIAL" else ""
        if aguarda:
            sufixo = f", {parcial}" if parcial else ""
            aguardando.append(f"{codigo} {mes_br(c.competencia)} ({(hoje - c.vencimento).days} dias{sufixo})")
        else:
            yield Item("ATRASADA", f"{codigo} — aluguel de {mes_br(c.competencia)} atrasado",
                       f"Venceu em {data_br(c.vencimento)}; {parcial or f'saldo {brl(saldo(c))}'}.",
                       url_de("locacao", c.locacao_id), 1)
    if aguardando:
        yield Item("AGUARDANDO", f"{len(aguardando)} cobrança(s) aguardando", "; ".join(aguardando),
                   url_de("aguardando"), 1)


def _reajustes(hoje, config):
    for loc, ano in reajustes_devidos(hoje):
        yield Item("REAJUSTE_CALCULAR", f"{loc.imovel.codigo} — calcular reajuste de {ano}",
                   f"Índice {loc.get_indice_display()}.", f"{url_de('reajustes')}?ano={ano}", 2)
    estados = [
        ("REAJUSTE_ENVIAR", "enviar reajuste de {} ao proprietário",
         dict(valor_calculado__isnull=False, enviado_em__isnull=True, decisao="PENDENTE")),
        ("REAJUSTE_DECISAO", "reajuste de {} aguardando decisão", dict(enviado_em__isnull=False, decisao="PENDENTE")),
        ("REAJUSTE_APLICAR", "aplicar reajuste de {} aprovado", dict(decisao="APROVADO", aplicado_em__isnull=True)),
    ]
    for tipo, texto, filtro in estados:
        for r in Reajuste.objects.filter(**filtro).select_related("locacao__imovel"):
            novo = r.valor_aprovado if tipo == "REAJUSTE_APLICAR" else r.valor_calculado
            yield Item(tipo, f"{r.locacao.imovel.codigo} — {texto.format(r.ano)}",
                       f"{brl(r.valor_atual)} → {brl(novo)} a partir de {mes_br(r.vigente_desde)}.",
                       f"{url_de('reajustes')}?ano={r.ano}", 2)


def _contratos(hoje, config):
    limite = hoje + timedelta(days=config.aviso_termino_dias)
    for loc in Locacao.objects.filter(ativa=True).select_related("imovel"):
        codigo, url = loc.imovel.codigo, url_de("locacao", loc.pk)
        if loc.termino and loc.termino < hoje:
            yield Item("CONTRATO_VENCIDO", f"{codigo} — contrato vencido", f"Terminou em {data_br(loc.termino)}.", url, 2)
        elif loc.termino and loc.termino <= limite:
            yield Item("CONTRATO_TERMINANDO", f"{codigo} — contrato terminando", f"Termina em {data_br(loc.termino)}.",
                       url, 3)
        if not loc.link_contrato:
            yield Item("SEM_CONTRATO", f"{codigo} — locação sem link do contrato", "", url, 3)


def _despesas(hoje, config):
    pendentes = (
        Despesa.objects.filter(informado_em__isnull=True).exclude(imovel__locacoes__ativa=True)
        .select_related("imovel").order_by("imovel__codigo", "competencia")
    )
    for d in pendentes:
        yield Item("DESPESA_INFORMAR", f"{d.imovel.codigo} — informar {d.get_tipo_display()} de {mes_br(d.competencia)}",
                   f"Imóvel vago; {brl(d.valor)}.", url_de("despesas"), 2)


def _transferencias(hoje, config):
    for im in Imovel.objects.exclude(proprietario_atual="").select_related("empresa"):
        atual, empresa = im.proprietario_atual.strip(), (im.empresa.nome.strip() if im.empresa else "")
        if atual and empresa and atual.casefold() != empresa.casefold():
            yield Item("TRANSFERENCIA", f"{im.codigo} — transferência pendente",
                       f"Proprietário atual: {atual}; empresa: {empresa}.", url_de("imovel", im.pk), 4)


def _comissoes(hoje, config):
    recebidas = set(ComissaoMes.objects.filter(recebida_em__isnull=False).values_list("mes", flat=True))
    mes, atual = primeiro_dia(config.inicio_historico), primeiro_dia(hoje)
    while mes < atual:
        if mes not in recebidas:
            valor = resumo_mes(mes, hoje, config).comissao
            if valor > 0:
                yield Item("COMISSAO", f"Comissão de {mes_br(mes)} a receber", brl(valor), url_de("comissoes"), 2)
        mes = somar_meses(mes, 1)


def _manuais(hoje, config):
    for t in TarefaManual.objects.filter(concluida=False).select_related("imovel").order_by(F("prazo").asc(nulls_last=True), "pk"):
        partes = [f"Prazo {data_br(t.prazo)}" if t.prazo else "", t.imovel.codigo if t.imovel else ""]
        vencida = t.prazo is not None and t.prazo < hoje
        yield Item("MANUAL", t.titulo, "; ".join(p for p in partes if p), url_de("tarefas"), 1 if vencida else 3,
                   reverse("tarefa_concluir", args=[t.pk]))


def _revisao(hoje, config):
    n = RevisaoImportacao.objects.filter(resolvida=False).count()
    if n:
        texto = "1 linha" if n == 1 else f"{n} linhas"
        yield Item("REVISAO", f"{texto} da importação para revisar", "", url_de("revisao"), 2)


def _empresas(hoje, config):
    if Empresa.objects.exists() and not Empresa.objects.filter(acesso_direto=True).exists():
        yield Item("EMPRESA_ACESSO", "Indique quais empresas têm acesso direto à conta",
                   "Sem isso, toda cobrança vencida aparece como aguardando o proprietário.", url_de("empresas"), 2)


FONTES = [_cobrancas, _dividas, _reajustes, _contratos, _despesas, _transferencias, _comissoes, _manuais, _revisao, _empresas]


def listar(hoje: date) -> list[Item]:
    gerar_cobrancas(hoje)
    config = Config.get()
    itens = [item for fonte in FONTES for item in fonte(hoje, config)]
    return sorted(itens, key=lambda i: i.prioridade)  # estável: mantém a ordem de cada fonte (ex.: mês)
