import calendar
from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from .dinheiro import primeiro_dia, somar_meses
from .models import Alocacao, Cobranca, Config, Locacao, Pagamento
from .templatetags.br import mes_br


def vencimento_de(locacao, competencia: date) -> date:
    ultimo = calendar.monthrange(competencia.year, competencia.month)[1]
    return competencia.replace(day=min(locacao.dia_vencimento, ultimo))


def _primeira_competencia(locacao) -> date:
    config = Config.get()
    primeira = max(primeiro_dia(locacao.inicio), primeiro_dia(config.inicio_historico))
    if config.gerar_a_partir_de and locacao.importada:  # veio da planilha: meses anteriores são dela
        primeira = max(primeira, primeiro_dia(config.gerar_a_partir_de))
    return primeira


def _criar_cobranca(locacao, competencia: date):
    """Cria a cobrança do mês; None se não há valor vigente."""
    vencimento = vencimento_de(locacao, competencia)
    if competencia < somar_meses(primeiro_dia(locacao.inicio), locacao.isencao_meses):
        valor, motivo = Decimal("0"), "isenção"
    else:
        valor, motivo = locacao.valor_em(vencimento), ""
        if valor is None:
            return None
    try:
        with transaction.atomic():
            return Cobranca.objects.create(
                locacao=locacao, competencia=competencia, vencimento=vencimento, valor_esperado=valor,
                motivo_ajuste=motivo,
            )
    except IntegrityError:  # outro processo criou o mesmo mês ao mesmo tempo
        return Cobranca.objects.get(locacao=locacao, competencia=competencia)


def gerar_cobrancas(ate: date, locacao: Locacao | None = None) -> int:
    locacoes = [locacao] if locacao else Locacao.objects.filter(ativa=True)
    criadas = 0
    for loc in locacoes:
        if not loc.ativa:
            continue
        fim = primeiro_dia(ate)
        if loc.data_saida:
            fim = min(fim, primeiro_dia(loc.data_saida))
        existentes = set(loc.cobrancas.values_list("competencia", flat=True))
        comp = _primeira_competencia(loc)
        while comp <= fim:
            if comp not in existentes and _criar_cobranca(loc, comp):
                criadas += 1
            comp = somar_meses(comp, 1)
    return criadas


def atualizar_vencimentos(locacao, hoje: date) -> None:
    """Novo dia de vencimento vale para as cobranças em aberto (sem pagamento) do mês atual em diante."""
    for c in locacao.cobrancas.filter(competencia__gte=primeiro_dia(hoje), quitada=False, alocacoes__isnull=True):
        c.vencimento = vencimento_de(locacao, c.competencia)
        c.save(update_fields=["vencimento"])


def situacao(cobranca, hoje: date) -> str:
    if cobranca.valor_esperado == 0:
        return "ISENTA"
    recebido = cobranca.recebido
    if cobranca.quitada or recebido >= cobranca.valor_esperado + cobranca.ajuste:
        return "PAGA"
    if recebido > 0:
        return "PARCIAL"
    if hoje <= cobranca.vencimento:
        return "ABERTO"
    empresa = cobranca.locacao.imovel.empresa
    return "AGUARDANDO" if empresa and not empresa.acesso_direto else "ATRASADA"


def aguarda_proprietario(cobranca, situacao: str, hoje: date) -> bool:
    """Vencida sem acesso à conta da empresa (inclusive parcial): depende do proprietário, não é dívida."""
    if situacao == "AGUARDANDO":
        return True
    empresa = cobranca.locacao.imovel.empresa
    return situacao == "PARCIAL" and hoje > cobranca.vencimento and bool(empresa) and not empresa.acesso_direto


def saldo(cobranca) -> Decimal:
    if cobranca.quitada:
        return Decimal("0")
    return max(Decimal("0"), cobranca.valor_esperado + cobranca.ajuste - cobranca.recebido)


PONTUALIDADE = "desconto por pontualidade"


def _no_prazo_do_desconto(locacao, cobranca, data: date | None) -> bool:
    if not (locacao.desconto_pontualidade and locacao.dia_limite_desconto and data):
        return False
    ultimo = calendar.monthrange(cobranca.competencia.year, cobranca.competencia.month)[1]
    return data <= cobranca.competencia.replace(day=min(locacao.dia_limite_desconto, ultimo))


def _plano_fifo(locacao, abertas, valor: Decimal, data: date | None) -> list[tuple[Cobranca, Decimal]]:
    """Quanto vai para cada mês em aberto. Plano A: saldo cheio, mês a mês. Se A deixa o último mês tocado
    incompleto, plano B: os meses tocados por A que estão no prazo do desconto pedem saldo − desconto; se B
    cobre todos eles, vale B (o resto segue pelo saldo cheio). Senão, A. Quem paga cheio nunca leva desconto."""
    def montar(com_desconto: int):
        resta, plano = valor, []
        for i, c in enumerate(abertas):
            if resta <= 0:
                break
            s = saldo(c)
            if i < com_desconto and s > (locacao.desconto_pontualidade or 0) \
                    and _no_prazo_do_desconto(locacao, c, data):
                s -= locacao.desconto_pontualidade
            parte = min(s, resta)
            plano.append((c, parte, parte == s))
            resta -= parte
        return plano

    a = montar(0)
    if a and not a[-1][2]:
        b = montar(len(a))
        if len(b) >= len(a) and all(completo for _, _, completo in b[:len(a)]):
            a = b
    return [(c, parte) for c, parte, _ in a]


def _alocar(pagamento, cobranca, valor: Decimal) -> None:
    Alocacao.objects.create(pagamento=pagamento, cobranca=cobranca, valor=valor)
    getattr(cobranca, "_prefetched_objects_cache", {}).pop("alocacoes", None)  # recebido fresco
    resta, loc = saldo(cobranca), pagamento.locacao
    if 0 < resta <= (loc.desconto_pontualidade or 0) and _no_prazo_do_desconto(loc, cobranca, pagamento.data):
        cobranca.ajuste -= loc.desconto_pontualidade  # soma a um ajuste que já houver
        cobranca.motivo_ajuste = "; ".join(m for m in (cobranca.motivo_ajuste, PONTUALIDADE) if m)
        cobranca.quitada = True
        cobranca.save(update_fields=["ajuste", "motivo_ajuste", "quitada"])
    elif resta == 0:
        cobranca.quitada = True
        cobranca.save(update_fields=["quitada"])


@transaction.atomic
def registrar_pagamento(locacao, data: date | None, valor: Decimal, observacoes: str = "",
                        alocacoes: list[tuple[Cobranca, Decimal]] | None = None, desde: date | None = None) -> Pagamento:
    """Com alocações: só elas. Sem: FIFO pelos meses em aberto (a partir de `desde`, se dado), depois antecipa
    meses futuros."""
    if alocacoes is not None:
        if sum((v for _, v in alocacoes), Decimal("0")) > valor:
            raise ValueError("Alocações somam mais que o valor do pagamento.")
        if any(c.locacao_id != locacao.pk or v <= 0 for c, v in alocacoes):
            raise ValueError("Alocação inválida: cobrança de outra locação ou valor não positivo.")
    pagamento = Pagamento.objects.create(locacao=locacao, data=data, valor=valor, observacoes=observacoes)
    if alocacoes is not None:
        for cobranca, v in alocacoes:
            _alocar(pagamento, cobranca, v)
        return pagamento

    resta = valor
    abertas = locacao.cobrancas.filter(quitada=False, valor_esperado__gt=0).order_by("competencia")
    if desde:
        abertas = abertas.filter(competencia__gte=desde)
    for cobranca, parte in _plano_fifo(locacao, list(abertas), valor, data):
        if parte == 0:  # ajuste já zerou o saldo
            cobranca.quitada = True
            cobranca.save(update_fields=["quitada"])
            continue
        _alocar(pagamento, cobranca, parte)
        resta -= parte
    if resta <= 0:
        return pagamento

    # Antecipação: gera e paga meses futuros.
    if not locacao.ativa:
        return pagamento
    dia = data or timezone.localdate()
    if locacao.termino and locacao.termino >= dia:
        limite = primeiro_dia(locacao.termino)
    else:  # sem término, ou prorrogação tácita
        limite = somar_meses(primeiro_dia(dia), 24)
    if locacao.data_saida:
        limite = min(limite, primeiro_dia(locacao.data_saida))
    ultima = locacao.cobrancas.order_by("-competencia").first()
    comp = somar_meses(ultima.competencia, 1) if ultima else _primeira_competencia(locacao)
    while resta > 0 and comp <= limite:
        cobranca = _criar_cobranca(locacao, comp)
        if cobranca is None:
            break
        parte = min(saldo(cobranca), resta)  # antecipação: saldo cheio
        if parte > 0:
            _alocar(pagamento, cobranca, parte)
            resta -= parte
        comp = somar_meses(comp, 1)
    return pagamento


@transaction.atomic
def encerrar_locacao(locacao, data_saida: date) -> None:
    """Saída retroativa: apaga os meses gerados depois da saída; recusa se algum deles já recebeu pagamento."""
    depois = locacao.cobrancas.filter(competencia__gt=primeiro_dia(data_saida))
    pagos = sorted({c.competencia for c in depois.filter(alocacoes__isnull=False)})
    if pagos:
        raise ValueError(
            "Há pagamento lançado em mês posterior à saída (" + ", ".join(mes_br(m) for m in pagos)
            + "). Corrija o pagamento ou use uma data de saída mais tarde."
        )
    depois.delete()
    locacao.ativa, locacao.data_saida, locacao.editada = False, data_saida, True
    locacao.save(update_fields=["ativa", "data_saida", "editada"])


@transaction.atomic
def excluir_pagamento(pagamento, hoje: date) -> None:
    """Apaga o pagamento (alocações em cascata), reabre os meses que ele quitava e apaga os meses futuros
    criados pela antecipação que ficaram sem pagamento e sem edição."""
    loc = pagamento.locacao
    afetadas = list(Cobranca.objects.filter(alocacoes__pagamento=pagamento).distinct())
    pagamento.delete()
    Locacao.objects.filter(pk=loc.pk).update(editada=True)
    for c in afetadas:
        campos = ["quitada"]
        if PONTUALIDADE in c.motivo_ajuste:  # o desconto dependia deste pagamento
            c.ajuste += loc.desconto_pontualidade or 0
            c.motivo_ajuste = "; ".join(m for m in c.motivo_ajuste.split("; ") if m != PONTUALIDADE)
            campos += ["ajuste", "motivo_ajuste"]
        c.quitada = False
        c.quitada = saldo(c) == 0
        c.save(update_fields=campos)
    ultima = loc.cobrancas.filter(alocacoes__isnull=False).order_by("-competencia").first()
    limite = max(primeiro_dia(hoje), ultima.competencia if ultima else date.min)
    loc.cobrancas.filter(competencia__gt=limite, alocacoes__isnull=True, quitada=False, ajuste=0,
                         observacoes="").delete()


@transaction.atomic
def excluir_valor(valor, hoje: date) -> None:
    """Só valor futuro (não o inicial). Cobranças sem pagamento que o usavam voltam ao valor anterior."""
    if valor.motivo == "INICIAL" or valor.vigente_desde <= hoje:
        raise ValueError("Só se exclui valor futuro; o valor inicial e os já vigentes ficam no histórico.")
    loc = valor.locacao
    usadas = list(loc.cobrancas.filter(competencia__gte=primeiro_dia(valor.vigente_desde), quitada=False,
                                       alocacoes__isnull=True, valor_esperado=valor.valor))
    valor.delete()
    Locacao.objects.filter(pk=loc.pk).update(editada=True)
    loc.reajustes.filter(vigente_desde=valor.vigente_desde, aplicado_em__isnull=False).update(aplicado_em=None)
    for c in usadas:
        c.valor_esperado = loc.valor_em(c.vencimento) or Decimal("0")
        c.save(update_fields=["valor_esperado"])
