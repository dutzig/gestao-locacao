from datetime import date, datetime
from decimal import Decimal
from itertools import groupby

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import F, Prefetch, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.views.decorators.http import require_POST

from . import forms, indices, tarefas as tarefas_mod
from .apuracao import ZERO, mes_da_comissao, pela_data, resumo_mes, resumo_periodo, totais
from .cobrancas import (
    aguarda_proprietario, atualizar_vencimentos, encerrar_locacao, excluir_pagamento, excluir_valor, gerar_cobrancas,
    registrar_pagamento, saldo, situacao,
)
from .dinheiro import primeiro_dia, somar_meses
from .models import (
    AnotacaoDivida, Cobranca, ComissaoMes, Config, Despesa, Empresa, Imovel, Inquilino, Locacao, Pagamento,
    Reajuste, RevisaoImportacao, TarefaManual, ValorAluguel,
)
from .permissoes import admin_required
from .templatetags.br import VAZIO, brl, data_br, mes_br

SITUACOES = {
    "PAGA": "Paga", "PARCIAL": "Parcial", "ISENTA": "Isenta", "ABERTO": "Em aberto", "ATRASADA": "Atrasada",
    "AGUARDANDO": "Aguardando",
}


def saude(request):
    return HttpResponse("ok", content_type="text/plain")


def _linhas(linhas, hoje):
    """(linha, classe, rótulo); parcial vencida sem acesso aparece como aguardando, como em /aguardando/."""
    return [
        (l, "aguardando", "Aguardando (parcial)")
        if l.situacao == "PARCIAL" and aguarda_proprietario(l.cobranca, l.situacao, hoje)
        else (l, l.situacao.lower(), SITUACOES[l.situacao])
        for l in linhas
    ]


@login_required
def dashboard(request):
    hoje = timezone.localdate()
    gerar_cobrancas(hoje)  # mês atual completo (idempotente)
    try:
        mes = datetime.strptime(request.GET.get("mes", ""), "%Y-%m").date()
        if not date.min.year < mes.year < date.max.year:  # anterior/próximo precisam caber em date
            raise ValueError
    except ValueError:
        mes = primeiro_dia(hoje)
    r = resumo_mes(mes, hoje)
    return render(request, "locacao/dashboard.html", {
        "r": r, "pendentes": _linhas(r.pendentes, hoje), "recebidos": _linhas(r.recebidos, hoje),
        "anterior": somar_meses(mes, -1), "proximo": somar_meses(mes, 1),
        "regra": "por data de pagamento" if r.regra_nova else "por mês de referência",
    })


@login_required
def periodo(request):
    hoje = timezone.localdate()
    gerar_cobrancas(hoje)
    inicio_hist = primeiro_dia(Config.get().inicio_historico)
    ano = request.GET.get("ano", "")
    ano = int(ano) if ano.isdigit() and inicio_hist.year <= int(ano) <= hoje.year else hoje.year
    semestre = {"1": 1, "2": 2}.get(request.GET.get("semestre"))
    inicio = date(ano, 7 if semestre == 2 else 1, 1)
    fim = date(ano, 6 if semestre == 1 else 12, 1)
    # só meses do histórico até o mês atual: antes não há dado, depois só cobranças antecipadas
    resumos = resumo_periodo(max(inicio, inicio_hist), min(fim, primeiro_dia(hoje)), hoje)
    return render(request, "locacao/periodo.html", {
        "resumos": resumos, "total": totais(resumos), "ano": ano, "semestre": semestre,
        "anos": range(hoje.year, inicio_hist.year - 1, -1),
    })


@login_required
def inicio(request):
    return redirect("tarefas" if request.user.is_staff else "dashboard")


PRIORIDADES = {1: "Urgente", 2: "Importante", 3: "Acompanhar", 4: "Sem pressa"}


@admin_required
def tarefas(request):
    itens = tarefas_mod.listar(timezone.localdate())  # já ordenado por prioridade
    grupos = [(PRIORIDADES[p], list(g)) for p, g in groupby(itens, key=lambda i: i.prioridade)]
    return render(request, "locacao/tarefas.html", {"grupos": grupos, "form": forms.TarefaForm()})


# --- genéricos ---

def _lista(request, titulo, novo_url, novo_rotulo, colunas, linhas, vazio, busca):
    """colunas: rótulo, ou (rótulo, "num") p/ alinhar à direita; linhas: {"url", "celulas", "acao"?}."""
    classes = [c[1] if isinstance(c, tuple) else "" for c in colunas]
    for linha in linhas:
        linha["celulas"] = list(zip(linha["celulas"], classes))
    return render(request, "locacao/lista.html", {
        "titulo": titulo, "novo_url": novo_url, "novo_rotulo": novo_rotulo, "linhas": linhas, "vazio": vazio,
        "colunas": [c if isinstance(c, tuple) else (c, "") for c in colunas], "q": request.GET.get("q", ""),
        "busca": busca, "com_acao": any("acao" in linha for linha in linhas),
    })


def _form(request, form, titulo, voltar, sucesso, destino, grupos=None, salvar=None, **extra):
    """Valida e salva `form` no POST; `destino(obj)` dá a URL de retorno."""
    if request.method == "POST" and form.is_valid():
        obj = (salvar or form.save)()
        messages.success(request, sucesso)
        return redirect(destino(obj))
    if grupos:
        grupos = [(legenda, [form[n] for n in nomes]) for legenda, nomes in grupos]
    return render(request, "locacao/form.html", {
        "form": form, "titulo": titulo, "voltar": voltar, "grupos": grupos, **extra,
    })


def _valor(obj, nome):
    f = obj._meta.get_field(nome)
    v = getattr(obj, nome)
    if f.choices:
        v = getattr(obj, f"get_{nome}_display")()
    if isinstance(v, bool):
        return "Sim" if v else "Não"
    if hasattr(v, "strftime"):
        return data_br(v)
    if isinstance(v, Decimal):
        return f"{v:.2f}".replace(".", ",") + " %" if nome.endswith("pct") else brl(v)
    return str(v) if v not in (None, "") else VAZIO


def _dados(obj, nomes):
    return [(obj._meta.get_field(n).verbose_name, _valor(obj, n)) for n in nomes]


def _cobrancas(loc, hoje):
    """Cobranças com situação; saldos separados: dívida (vencida, atrasada/parcial), aguardando proprietário, resto."""
    if loc.ativa:
        gerar_cobrancas(hoje, locacao=loc)
    linhas, divida, aguardando, aberto = [], Decimal("0"), Decimal("0"), Decimal("0")
    for c in loc.cobrancas.prefetch_related("alocacoes"):
        c.locacao = loc  # situacao() lê locacao.imovel.empresa
        s, resta = situacao(c, hoje), saldo(c)
        linhas.append((c, s.lower(), SITUACOES[s], resta))
        if aguarda_proprietario(c, s, hoje):
            aguardando += resta
        else:
            aberto += resta
            if s in ("ATRASADA", "PARCIAL") and c.vencimento < hoje:
                divida += resta
    return linhas, divida, aguardando, aberto


@transaction.atomic
def _salvar_editada(form, locacao_pk):
    """Salva um formulário ligado a uma locação e a marca como editada à mão."""
    obj = form.save()
    Locacao.objects.filter(pk=locacao_pk).update(editada=True)
    return obj


def _post(request):
    return request.POST if request.method == "POST" else None


def _q(request, *campos):
    termo = request.GET.get("q", "").strip()
    filtro = Q()
    for c in campos:
        filtro |= Q(**{f"{c}__icontains": termo})
    return filtro if termo else Q()


# --- empresas ---

@admin_required
def empresas(request):
    linhas = [
        {"url": reverse("empresa_editar", args=[e.pk]), "celulas": [e.nome, e.cnpj or VAZIO, e.banco or VAZIO,
                                                                    "Sim" if e.acesso_direto else "Não"]}
        for e in Empresa.objects.filter(_q(request, "nome"))
    ]
    return _lista(request, "Empresas", reverse("empresa_nova"), "Nova empresa",
                  ["Nome", "CNPJ", "Banco", "Acesso direto"], linhas, "Nenhuma empresa cadastrada.", "Buscar por nome")


@admin_required
def empresa_form(request, pk=None):
    empresa = pk and get_object_or_404(Empresa, pk=pk)
    form = forms.EmpresaForm(_post(request), instance=empresa)
    form.instance.importado = False  # editado à mão: a reimportação exige --forcar
    return _form(request, form, f"Editar {empresa}" if empresa else "Nova empresa", reverse("empresas"),
                 "Empresa salva.", lambda _: reverse("empresas"))


# --- imóveis ---

@admin_required
def imoveis(request):
    ativas = Prefetch("locacoes", Locacao.objects.filter(ativa=True).select_related("inquilino"), to_attr="ativas")
    qs = Imovel.objects.filter(_q(request, "codigo", "endereco", "cidade")).select_related("empresa").prefetch_related(ativas)
    linhas = [
        {"url": reverse("imovel", args=[i.pk]), "celulas": [
            i.codigo, ", ".join(p for p in (i.endereco, i.unidade, i.cidade) if p) or VAZIO,
            "Alugado" if i.ativas else "Vago", i.ativas[0].inquilino.nome if i.ativas else VAZIO,
        ]}
        for i in qs
    ]
    return _lista(request, "Imóveis", reverse("imovel_novo"), "Novo imóvel",
                  ["Código", "Endereço", "Status", "Inquilino atual"], linhas, "Nenhum imóvel cadastrado.",
                  "Buscar por código, endereço ou cidade")


@admin_required
def imovel(request, pk):
    imovel = get_object_or_404(Imovel.objects.select_related("empresa"), pk=pk)
    hoje = timezone.localdate()
    locacoes = []
    for loc in imovel.locacoes.select_related("inquilino").order_by("-inicio"):
        loc.imovel = imovel
        _, _, aguardando, aberto = _cobrancas(loc, hoje)
        locacoes.append((loc, aberto, aguardando))
    return render(request, "locacao/imovel_detalhe.html", {
        "imovel": imovel, "locacoes": locacoes,
        "grupos": [(titulo, _dados(imovel, nomes)) for titulo, nomes in forms.IMOVEL_GRUPOS],
    })


@admin_required
def imovel_form(request, pk=None):
    imovel = pk and get_object_or_404(Imovel, pk=pk)
    form = forms.ImovelForm(_post(request), instance=imovel)
    form.instance.importado = False  # editado à mão: a reimportação exige --forcar
    voltar = reverse("imovel", args=[pk]) if pk else reverse("imoveis")
    return _form(request, form, f"Editar imóvel {imovel}" if imovel else "Novo imóvel", voltar, "Imóvel salvo.",
                 lambda i: reverse("imovel", args=[i.pk]), grupos=forms.IMOVEL_GRUPOS)


# --- inquilinos ---

@admin_required
def inquilinos(request):
    linhas = [
        {"url": reverse("inquilino", args=[i.pk]), "celulas": [i.nome, i.documento or VAZIO, i.contato or VAZIO]}
        for i in Inquilino.objects.filter(_q(request, "nome", "nome2", "documento"))
    ]
    return _lista(request, "Inquilinos", reverse("inquilino_novo"), "Novo inquilino",
                  ["Nome", "CPF/CNPJ", "Contato"], linhas, "Nenhum inquilino cadastrado.",
                  "Buscar por nome ou CPF/CNPJ")


@admin_required
def inquilino(request, pk):
    inquilino = get_object_or_404(Inquilino, pk=pk)
    return render(request, "locacao/inquilino_detalhe.html", {
        "inquilino": inquilino,
        "dados": _dados(inquilino, ["nome", "documento", "nome2", "documento2", "contato", "observacoes"]),
        "locacoes": Locacao.objects.filter(inquilino=inquilino).select_related("imovel"),
    })


@admin_required
def inquilino_form(request, pk=None):
    inquilino = pk and get_object_or_404(Inquilino, pk=pk)
    form = forms.InquilinoForm(_post(request), instance=inquilino)
    form.instance.importado = False  # editado à mão: a reimportação exige --forcar
    voltar = reverse("inquilino", args=[pk]) if pk else reverse("inquilinos")
    return _form(request, form, f"Editar {inquilino}" if inquilino else "Novo inquilino", voltar, "Inquilino salvo.",
                 lambda i: reverse("inquilino", args=[i.pk]))


# --- locações ---

@admin_required
def locacao_nova(request):
    pk = request.GET.get("imovel", "")
    imovel = Imovel.objects.filter(pk=pk).first() if pk.isdigit() else None
    form = forms.LocacaoNovaForm(_post(request), initial={"imovel": imovel})

    @transaction.atomic
    def salvar():
        loc = form.save()
        ValorAluguel.objects.create(locacao=loc, vigente_desde=loc.inicio, valor=form.cleaned_data["valor_inicial"],
                                    motivo="INICIAL")
        return loc

    voltar = reverse("imovel", args=[imovel.pk]) if imovel else reverse("imoveis")
    return _form(request, form, "Nova locação", voltar, "Locação cadastrada.",
                 lambda loc: reverse("locacao", args=[loc.pk]), salvar=salvar, grupos=forms.LOCACAO_GRUPOS)


@admin_required
def locacao_editar(request, pk):
    loc = get_object_or_404(Locacao, pk=pk)
    form = forms.LocacaoForm(_post(request), instance=loc)
    loc.editada = True  # só é salvo se o formulário for válido; a reimportação passa a exigir --forcar
    grupos = [(t, [n for n in nomes if n != "valor_inicial"]) for t, nomes in forms.LOCACAO_GRUPOS]

    @transaction.atomic
    def salvar():
        loc = form.save()
        if "dia_vencimento" in form.changed_data:
            atualizar_vencimentos(loc, timezone.localdate())
        return loc

    return _form(request, form, f"Editar locação {loc}", reverse("locacao", args=[pk]), "Locação salva.",
                 lambda loc: reverse("locacao", args=[loc.pk]), grupos=grupos, salvar=salvar)


@admin_required
def locacao(request, pk):
    loc = get_object_or_404(Locacao.objects.select_related("imovel__empresa", "inquilino"), pk=pk)
    hoje = timezone.localdate()
    cobrancas, divida, aguardando, _ = _cobrancas(loc, hoje)
    vigente = loc.valores.filter(vigente_desde__lte=hoje).order_by("-vigente_desde").first()
    valores = [(v, v == vigente, v.vigente_desde > hoje and v.motivo != "INICIAL")
               for v in loc.valores.filter(vigente_desde__gte=vigente.vigente_desde if vigente else date.min)]
    anotacoes = list(loc.anotacoes.all())
    return render(request, "locacao/locacao_detalhe.html", {
        "loc": loc, "cobrancas": cobrancas, "valores": valores, "divida": divida, "aguardando": aguardando,
        "anotacoes": anotacoes, "form_situacao": forms.SituacaoDividaForm(instance=loc),
        "form_anotacao": forms.AnotacaoForm(initial={"data": hoje}),
        "pagamentos": loc.pagamentos.all(), "reajustes": loc.reajustes.all(),
        "mostra_divida": divida > 0 or anotacoes or loc.situacao_divida != "NENHUMA",
    })


@admin_required
def valor_novo(request, pk):
    loc = get_object_or_404(Locacao, pk=pk)
    form = forms.ValorForm(_post(request), instance=ValorAluguel(locacao=loc))
    if form.is_bound and form.is_valid() and loc.valores.filter(vigente_desde=form.cleaned_data["vigente_desde"]).exists():
        form.add_error("vigente_desde", "Já existe um valor cadastrado com esta data.")
    return _form(request, form, f"Novo valor — {loc}", reverse("locacao", args=[pk]), "Valor cadastrado.",
                 lambda _: reverse("locacao", args=[pk]), salvar=lambda: _salvar_editada(form, loc.pk),
                 ajuda="Use também para cadastrar de antemão o escalonamento previsto em contrato.")


@admin_required
def locacao_encerrar(request, pk):
    loc = get_object_or_404(Locacao, pk=pk)
    voltar = reverse("locacao", args=[pk])
    if not loc.ativa:
        messages.info(request, "Esta locação já está encerrada.")
        return redirect(voltar)
    form = forms.EncerrarForm(_post(request), initial={"data_saida": timezone.localdate()})
    if form.is_bound and form.is_valid():
        if form.cleaned_data["data_saida"] < loc.inicio:
            form.add_error("data_saida", "A data de saída não pode ser anterior ao início da locação.")
        else:
            try:
                encerrar_locacao(loc, form.cleaned_data["data_saida"])
            except ValueError as e:
                form.add_error("data_saida", str(e))
            else:
                messages.success(request, "Locação encerrada.")
                return redirect(voltar)
    return _form(request, form, f"Encerrar locação — {loc}", voltar, "", None, botao="Encerrar locação",
                 ajuda="A locação deixa de gerar cobranças. As cobranças em aberto continuam nela; "
                       "meses gerados depois da saída e sem pagamento são apagados.")


def _excluir(request, titulo, voltar, dados, texto, excluir, sucesso, aviso=""):
    """GET: página de confirmação; POST (do formulário dela): `excluir()`; ValueError vira mensagem."""
    if request.method != "POST":
        return render(request, "locacao/excluir.html",
                      {"titulo": titulo, "voltar": voltar, "dados": dados, "texto": texto, "aviso": aviso})
    try:
        excluir()
    except ValueError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, sucesso)
        if aviso:
            messages.warning(request, aviso)
    return redirect(voltar)


@admin_required
def pagamento_excluir(request, pk):
    p = get_object_or_404(Pagamento.objects.select_related("locacao"), pk=pk)
    meses = sorted({a.cobranca.competencia for a in p.alocacoes.select_related("cobranca")})
    corte = Config.get().mes_corte
    da_comissao = {mes_da_comissao(m, p.data, corte) for m in meses}
    if pela_data(p.data, corte):
        da_comissao.add(primeiro_dia(p.data))  # inclusive a parte não distribuída
    recebidas = ComissaoMes.objects.filter(mes__in=da_comissao, recebida_em__isnull=False).order_by("mes")
    aviso = "".join(f"Comissão de {mes_br(c.mes)} já marcada como recebida: o valor apurado desse mês vai mudar. "
                    for c in recebidas).strip()
    return _excluir(
        request, "Excluir pagamento", reverse("locacao", args=[p.locacao_id]),
        [("Locação", p.locacao), ("Data", data_br(p.data)), ("Valor", brl(p.valor)),
         ("Meses afetados", ", ".join(mes_br(m) for m in meses) or VAZIO)],
        "Os meses afetados voltam a ficar em aberto (salvo os que um ajuste já zera); meses futuros criados por "
        "antecipação e que ficarem sem pagamento são apagados.",
        lambda: excluir_pagamento(p, timezone.localdate()), "Pagamento excluído.", aviso,
    )


@admin_required
def valor_excluir(request, pk):
    v = get_object_or_404(ValorAluguel.objects.select_related("locacao"), pk=pk)
    return _excluir(
        request, "Excluir valor", reverse("locacao", args=[v.locacao_id]),
        [("Locação", v.locacao), ("Vigente desde", data_br(v.vigente_desde)), ("Valor", brl(v.valor)),
         ("Motivo", v.get_motivo_display())],
        "Cobranças sem pagamento que usavam este valor voltam ao valor anterior.",
        lambda: excluir_valor(v, timezone.localdate()), "Valor excluído.",
    )


# --- despesas ---

@admin_required
def despesas(request):
    qs = Despesa.objects.filter(_q(request, "imovel__codigo", "descricao")).select_related("imovel").order_by(
        F("informado_em").desc(nulls_first=True), "-competencia")
    linhas = [
        {"url": reverse("despesa_editar", args=[d.pk]), "celulas": [
            d.imovel.codigo, d.get_tipo_display() + (f" — {d.descricao}" if d.descricao else ""), mes_br(d.competencia),
            data_br(d.vencimento), brl(d.valor), f"Informada em {data_br(d.informado_em)}" if d.informado_em else "Pendente",
        ], "acao": None if d.informado_em else {"url": reverse("despesa_informada", args=[d.pk]), "rotulo": "Marcar informada"}}
        for d in qs
    ]
    return _lista(request, "Despesas de imóvel vago", reverse("despesa_nova"), "Nova despesa",
                  ["Imóvel", "Tipo", "Competência", "Vencimento", ("Valor", "num"), "Informada ao proprietário"], linhas,
                  "Nenhuma despesa cadastrada.", "Buscar por código do imóvel ou descrição")


@admin_required
def despesa_form(request, pk=None):
    despesa = pk and get_object_or_404(Despesa, pk=pk)
    form = forms.DespesaForm(_post(request), instance=despesa)
    return _form(request, form, "Editar despesa" if despesa else "Nova despesa", reverse("despesas"), "Despesa salva.",
                 lambda _: reverse("despesas"))


@admin_required
@require_POST
def despesa_informada(request, pk):
    despesa = get_object_or_404(Despesa, pk=pk)
    despesa.informado_em = timezone.localdate()
    despesa.save(update_fields=["informado_em"])
    messages.success(request, "Despesa marcada como informada ao proprietário.")
    return redirect("despesas")


# --- tarefas manuais e configuração ---

@admin_required
def tarefa_nova(request):
    form = forms.TarefaForm(_post(request))
    return _form(request, form, "Nova tarefa", reverse("tarefas"), "Tarefa criada.", lambda _: reverse("tarefas"))


@admin_required
@require_POST
def tarefa_concluir(request, pk):
    tarefa = get_object_or_404(TarefaManual, pk=pk)
    tarefa.concluida = True
    tarefa.save(update_fields=["concluida"])
    messages.success(request, "Tarefa concluída.")
    return redirect("tarefas")


@admin_required
def config(request):
    form = forms.ConfigForm(_post(request), instance=Config.get())
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Configurações salvas.")
        if "mes_corte" in form.changed_data and ComissaoMes.objects.filter(recebida_em__isnull=False).exists():
            messages.warning(request, "O mês de corte mudou e há comissão já marcada como recebida: confira em "
                                      "Comissões se os valores desses meses continuam batendo.")
        return redirect("config")
    return render(request, "locacao/config.html", {"form": form, "titulo": "Configurações", "empresas": Empresa.objects.all()})


# --- pagamento, cobrança e dívida ---

def _msg_pagamento(request, p):
    n = p.alocacoes.filter(cobranca__quitada=True).count()
    texto = "Nenhum mês quitado" if n == 0 else "1 mês quitado" if n == 1 else f"{n} meses quitados"
    resto = p.nao_alocado
    if resto > 0:
        texto += f"; {brl(resto)} não foi distribuído a nenhum mês"
    messages.success(request, f"Pagamento registrado. {texto}.")


@admin_required
def pagamento_novo(request, pk):
    loc = get_object_or_404(Locacao.objects.select_related("imovel__empresa", "inquilino"), pk=pk)
    hoje = timezone.localdate()
    abertas = [(c, resta) for c, _, _, resta in _cobrancas(loc, hoje)[0] if resta > 0]
    form = forms.PagamentoForm(_post(request), abertas=abertas, initial={"data": hoje})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            p = registrar_pagamento(loc, d["data"], d["valor"], d["observacoes"], form.alocacoes())
        except ValueError as e:
            form.add_error(None, str(e))
        else:
            _msg_pagamento(request, p)
            return redirect("locacao", pk=pk)
    manual = form.is_bound and any(f.value() or f.errors for _, _, f in form.linhas())
    return render(request, "locacao/pagamento_form.html", {
        "form": form, "titulo": f"Registrar pagamento — {loc}", "voltar": reverse("locacao", args=[pk]),
        "botao": "Registrar pagamento", "manual_aberto": manual or bool(form.non_field_errors()),
    })


@admin_required
@require_POST
def cobranca_pagar(request, pk):
    """Pagamento rápido da linha: até o saldo vai para esta cobrança; acima dele, FIFO a partir deste mês (mesmo
    plano do pagamento automático, com o desconto de pontualidade). `saldo` é o da página: se mudou, recusa."""
    c = get_object_or_404(Cobranca, pk=pk)
    voltar = request.POST.get("voltar", "")
    if not url_has_allowed_host_and_scheme(voltar, {request.get_host()}, require_https=request.is_secure()):
        voltar = reverse("dashboard") + "?" + urlencode({"mes": c.competencia.strftime("%Y-%m")})
    form = forms.PagarCobrancaForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Pagamento não registrado. " + " ".join(
            f"{form[nome].label}: {' '.join(erros)}" for nome, erros in form.errors.items()))
        return redirect(voltar)
    d = form.cleaned_data
    with transaction.atomic():  # sqlite em transação imediata: a releitura do saldo já trava a escrita
        c = Cobranca.objects.select_related("locacao").get(pk=pk)
        resta = saldo(c)
        if c.valor_esperado == 0:
            messages.error(request, "Esta cobrança não tem valor a receber.")
        elif resta != d["saldo"]:
            messages.error(request, "A cobrança mudou desde que a página foi aberta; confira e tente de novo.")
        elif resta <= 0:
            messages.error(request, f"A cobrança de {mes_br(c.competencia)} já está quitada; nada foi registrado.")
        else:
            if d["valor"] <= resta:
                p = registrar_pagamento(c.locacao, d["data"], d["valor"], alocacoes=[(c, d["valor"])])
            else:
                p = registrar_pagamento(c.locacao, d["data"], d["valor"], desde=c.competencia)
            _msg_pagamento(request, p)
    return redirect(voltar)


@admin_required
def cobranca_editar(request, pk):
    c = get_object_or_404(Cobranca.objects.select_related("locacao"), pk=pk)
    voltar = reverse("locacao", args=[c.locacao_id])
    form = forms.CobrancaForm(_post(request), instance=c)
    return _form(request, form, f"Cobrança de {mes_br(c.competencia)} — {c.locacao}", voltar, "Cobrança salva.",
                 lambda _: voltar, salvar=lambda: _salvar_editada(form, c.locacao_id),
                 ajuda=f"Esperado {brl(c.valor_esperado)}; recebido {brl(c.recebido)}. Ajuste negativo = desconto.")


@admin_required
def anotacao_nova(request, pk):
    loc = get_object_or_404(Locacao, pk=pk)
    form = forms.AnotacaoForm(_post(request), instance=AnotacaoDivida(locacao=loc),
                              initial={"data": timezone.localdate()})
    voltar = reverse("locacao", args=[pk])
    return _form(request, form, f"Nova anotação da dívida — {loc}", voltar, "Anotação registrada.", lambda _: voltar)


@admin_required
@require_POST
def situacao_divida(request, pk):
    loc = get_object_or_404(Locacao, pk=pk)
    form = forms.SituacaoDividaForm(request.POST, instance=loc)
    loc.editada = True
    if form.is_valid():
        form.save()
        messages.success(request, "Situação da dívida salva.")
    else:
        messages.error(request, "Situação da dívida inválida.")
    return redirect("locacao", pk=pk)


# --- reajustes ---

MSG_INDICE = "Não foi possível obter o índice do Banco Central. Informe o percentual manualmente."


def _status_reajuste(r):
    if r.aplicado_em:
        return "Aplicado"
    if r.decisao == "RECUSADO":
        return "Recusado"
    if r.decisao == "APROVADO":
        return "Aprovado — aplicar"
    return "Aguardando decisão" if r.enviado_em else "Calculado — enviar ao proprietário"


@admin_required
def reajustes(request):
    hoje = timezone.localdate()
    pedido = request.GET.get("ano", "")
    ano = int(pedido) if pedido.isdigit() and len(pedido) == 4 else hoje.year
    manual = request.GET.get("manual", "")
    linhas = [{"loc": loc, "r": None} for loc, a in indices.reajustes_devidos(hoje) if a == ano]
    for r in Reajuste.objects.filter(ano=ano).select_related("locacao__imovel").order_by("locacao__imovel__codigo"):
        linhas.append({"loc": r.locacao, "r": r, "status": _status_reajuste(r)})
    if manual.isdigit() and not any(str(l["loc"].pk) == manual for l in linhas):
        linhas += [{"loc": loc, "r": None} for loc in Locacao.objects.filter(pk=manual).select_related("imovel")]
    for linha in linhas:
        linha["manual"] = str(linha["loc"].pk) == manual
    return render(request, "locacao/reajustes.html", {
        "ano": ano, "anterior": ano - 1, "seguinte": ano + 1, "linhas": linhas, "msg_indice": MSG_INDICE,
    })


def _buscar(*args):
    """Busca no BCB; qualquer falha da busca vira IndiceIndisponivel (o resto do ValueError é regra de negócio)."""
    try:
        return indices.buscar_variacoes(*args)  # lido na hora: testes trocam a função
    except ValueError as e:
        raise indices.IndiceIndisponivel(str(e)) from e


def _volta_reajustes(ano, **extra):
    return redirect(reverse("reajustes") + "?" + urlencode({"ano": ano, **extra}))


@admin_required
@require_POST
def reajuste_calcular(request, locacao_pk):
    loc = get_object_or_404(Locacao, pk=locacao_pk)
    form = forms.CalcularReajusteForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Ano ou percentual inválido.")
        return _volta_reajustes(request.POST.get("ano", ""), manual=loc.pk)
    ano, pct = form.cleaned_data["ano"], form.cleaned_data["percentual"]
    if loc.mes_base_reajuste is None or loc.indice == "NENHUM":
        messages.error(request, "Esta locação não tem índice ou mês-base de reajuste.")
        return _volta_reajustes(ano)
    existente = Reajuste.objects.filter(locacao=loc, ano=ano).first()
    if existente and (existente.enviado_em or existente.decisao != "PENDENTE"):
        messages.error(request, "Este reajuste já foi enviado ao proprietário ou decidido; não pode ser recalculado.")
        return _volta_reajustes(ano)
    try:
        indices.calcular_reajuste(loc, ano, percentual_manual=pct, buscar=_buscar)
    except indices.IndiceIndisponivel:
        messages.error(request, MSG_INDICE)
        return _volta_reajustes(ano, manual=loc.pk)
    except ValueError as e:
        messages.error(request, f"Não foi possível calcular: {e}.")
    else:
        messages.success(request, f"Reajuste de {ano} calculado para {loc.imovel}.")
    return _volta_reajustes(ano)


def _acao_reajuste(request, pk, acao, sucesso):
    r = get_object_or_404(Reajuste.objects.select_related("locacao"), pk=pk)
    if r.aplicado_em:
        messages.error(request, "Este reajuste já foi aplicado.")
    else:
        try:
            erro = acao(r, timezone.localdate())
        except ValueError as e:
            erro = str(e)
        if erro:
            messages.error(request, erro)
        else:
            messages.success(request, sucesso)
    return _volta_reajustes(r.ano)


@admin_required
@require_POST
def reajuste_enviado(request, pk):
    return _acao_reajuste(request, pk, indices.marcar_enviado, "Reajuste marcado como enviado ao proprietário.")


@admin_required
@require_POST
def reajuste_aprovar(request, pk):
    form = forms.AprovarReajusteForm(request.POST)

    def aprovar(r, hoje):
        if not form.is_valid():
            return "Informe o valor aprovado."
        indices.decidir(r, True, hoje, form.cleaned_data["valor_aprovado"])

    return _acao_reajuste(request, pk, aprovar, "Reajuste aprovado.")


@admin_required
@require_POST
def reajuste_recusar(request, pk):
    return _acao_reajuste(request, pk, lambda r, hoje: indices.decidir(r, False, hoje), "Reajuste recusado.")


@admin_required
def reajuste_aplicar(request, pk):
    """GET: página de confirmação; POST (do formulário dela): aplica."""
    if request.method != "POST":
        r = get_object_or_404(Reajuste.objects.select_related("locacao__imovel"), pk=pk)
        if r.decisao != "APROVADO" or r.aplicado_em:
            messages.error(request, "Só se aplica reajuste aprovado e ainda não aplicado.")
            return _volta_reajustes(r.ano)
        return render(request, "locacao/reajuste_aplicar.html", {"r": r})
    return _acao_reajuste(request, pk, indices.aplicar, "Reajuste aplicado: novo valor do aluguel cadastrado.")


# --- comissões ---

@admin_required
def comissoes(request):
    hoje, config = timezone.localdate(), Config.get()
    recebidas = {c.mes: c for c in ComissaoMes.objects.filter(recebida_em__isnull=False)}
    meses, mes = [], primeiro_dia(config.inicio_historico)
    while mes <= primeiro_dia(hoje):
        r = resumo_mes(mes, hoje, config)
        c = recebidas.get(mes)
        meses.append({"mes": mes, "comissao": r.comissao, "regra_nova": r.regra_nova, "recebida_em": c and c.recebida_em,
                      "valor_recebido": c and c.valor_recebido, "fechado": mes < primeiro_dia(hoje),
                      "diferenca": r.comissao - c.valor_recebido if c and c.valor_recebido is not None else ZERO})
        mes = somar_meses(mes, 1)
    pendentes = any(m["fechado"] and m["comissao"] and not m["recebida_em"] for m in meses)
    return render(request, "locacao/comissoes.html",
                  {"meses": meses[::-1], "config": config, "hoje": hoje, "pendentes": pendentes})


@admin_required
@require_POST
def comissoes_recebidas_ate(request):
    """Marca de uma vez os meses fechados com comissão até `ate` (atrasados da importação)."""
    form = forms.ComissoesAteForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Informe o mês e a data em que as comissões foram recebidas.")
        return redirect("comissoes")
    hoje, config = timezone.localdate(), Config.get()
    recebidas = set(ComissaoMes.objects.filter(recebida_em__isnull=False).values_list("mes", flat=True))
    mes = primeiro_dia(config.inicio_historico)
    fim = min(primeiro_dia(form.cleaned_data["ate"]), somar_meses(primeiro_dia(hoje), -1))  # só meses fechados
    n = 0
    while mes <= fim:
        valor = resumo_mes(mes, hoje, config).comissao if mes not in recebidas else ZERO
        if valor > 0:
            ComissaoMes.objects.update_or_create(
                mes=mes, defaults={"recebida_em": form.cleaned_data["recebida_em"], "valor_recebido": valor})
            n += 1
        mes = somar_meses(mes, 1)
    messages.success(request, "1 mês marcado como recebido." if n == 1 else f"{n} meses marcados como recebidos.")
    return redirect("comissoes")


@admin_required
@require_POST
def comissao_recebida(request, mes):
    try:
        d = datetime.strptime(mes, "%Y-%m").date()
    except ValueError:
        raise Http404
    if not primeiro_dia(Config.get().inicio_historico) <= d < primeiro_dia(timezone.localdate()):
        raise Http404  # só meses fechados do histórico
    form = forms.ComissaoRecebidaForm(request.POST)
    if form.is_valid():
        valor = resumo_mes(d, timezone.localdate()).comissao
        ComissaoMes.objects.update_or_create(
            mes=d, defaults={"recebida_em": form.cleaned_data["recebida_em"], "valor_recebido": valor})
        messages.success(request, f"Comissão de {mes_br(d)} marcada como recebida.")
    else:
        messages.error(request, "Informe a data em que a comissão foi recebida.")
    return redirect("comissoes")


@admin_required
@require_POST
def comissao_desfazer(request, mes):
    try:
        d = datetime.strptime(mes, "%Y-%m").date()
    except ValueError:
        raise Http404
    if ComissaoMes.objects.filter(mes=d, recebida_em__isnull=False).update(recebida_em=None, valor_recebido=None):
        messages.success(request, f"Comissão de {mes_br(d)} voltou a ficar a receber.")
    return redirect("comissoes")


# --- aguardando proprietário e revisão da importação ---

@admin_required
def aguardando(request):
    hoje = timezone.localdate()
    gerar_cobrancas(hoje)
    itens = sorted(((c, s) for c, s, aguarda in tarefas_mod.vencidas(hoje) if aguarda),
                   key=lambda cs: (cs[0].locacao.imovel.codigo, cs[0].competencia))
    grupos = [
        (imovel, [{"c": c, "dias": (hoje - c.vencimento).days, "saldo": saldo(c), "rotulo": "Aguardando (parcial)" if s == "PARCIAL" else SITUACOES[s]} for c, s in g])
        for imovel, g in groupby(itens, key=lambda cs: cs[0].locacao.imovel)
    ]
    return render(request, "locacao/aguardando.html", {"grupos": grupos})


@admin_required
def dividas(request):
    gerar_cobrancas(timezone.localdate())
    cobrancas = tarefas_mod.dividas(timezone.localdate())
    grupos = []
    for loc, g in groupby(cobrancas, key=lambda c: c.locacao):
        linhas = [(c, saldo(c)) for c in g]
        grupos.append((loc, linhas, sum((s for _, s in linhas), Decimal("0"))))
    return render(request, "locacao/dividas.html", {"grupos": grupos, "total": sum((t for *_, t in grupos), Decimal("0"))})


@admin_required
def revisao(request):
    return render(request, "locacao/revisao.html", {"itens": RevisaoImportacao.objects.filter(resolvida=False)})


@admin_required
@require_POST
def revisao_resolver(request, pk):
    item = get_object_or_404(RevisaoImportacao, pk=pk)
    item.resolvida = True
    item.save(update_fields=["resolvida"])
    messages.success(request, f"Linha {item.linha} da aba {item.aba} marcada como resolvida.")
    return redirect("revisao")
