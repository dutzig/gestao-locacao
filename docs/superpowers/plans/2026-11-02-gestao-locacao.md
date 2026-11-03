# Gestão de Locação Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sistema web (admin + visualização) que substitui a planilha de gestão de locação: locações com histórico próprio, cobranças, pagamentos, comissão, reajuste, despesas de imóvel vago, lista de tarefas e dashboard.

**Architecture:** Um projeto Django com um único app `locacao`. Regras de negócio em módulos puros e testados (`dinheiro`, `cobrancas`, `apuracao`, `indices`, `tarefas`, `importacao`); views finas renderizadas no servidor; CSS próprio sem build. SQLite num volume Docker, container atrás do Traefik existente na VPS.

**Tech Stack:** Python 3.12, Django 5.2 (LTS), gunicorn 26, whitenoise 6, openpyxl 3.1, SQLite. Testes com `python manage.py test` (unittest do Django, sem pytest).

**Spec:** `docs/superpowers/specs/2026-11-02-gestao-locacao-design.md` — leia antes de qualquer task.

## Global Constraints

- **Nenhum dado real no repositório.** Nada de nomes, CPF/CNPJ, telefones, valores, códigos de imóvel reais ou hostname da VPS em código, testes, fixtures, migrations, commits ou docs. Testes usam dados inventados (ex.: "Inquilino Teste", "UF-001", "000.000.000-00").
- Dinheiro sempre `Decimal` (`DecimalField(max_digits=12, decimal_places=2)`), nunca `float`. Arredondamento `ROUND_HALF_UP` para centavos.
- `LANGUAGE_CODE="pt-br"`, `TIME_ZONE="America/Sao_Paulo"`, `USE_TZ=True`. Exibição: `R$ 1.234,56`, `dd/mm/aaaa`, mês como `ago/2026`.
- Configuração por variáveis de ambiente: `DJANGO_SECRET_KEY`, `DJANGO_DEBUG` (padrão `0`), `APP_HOST`, `DB_PATH` (padrão `BASE_DIR/db.sqlite3`).
- `.gitignore` cobre: `*.sqlite3`, `*.xlsx`, `*.age`, `.env`, `backups/`, `dados/`.
- Interface em português. Visual sóbrio: fundo `#f7f7f5`, superfícies brancas, texto `#1f2328`, uma cor de destaque `#2f5d62`, status sempre com texto além da cor, contraste AA, foco visível, funciona em 360 px de largura.
- Dois perfis: `is_staff=True` = administrador (tudo); `is_staff=False` = visualização (só dashboard e visão anual).
- Apenas dependências: Django, gunicorn, whitenoise, openpyxl. Nada mais.
- Commits pequenos, mensagens Conventional Commits em inglês.

## Review Focus

1. **Pagamento antecipado de vários meses** — um pagamento maior que o mês aberto deve quitar os meses seguintes (criando as cobranças futuras se preciso) e a comissão cair inteira no mês do pagamento (após o corte). Teste em Task 3 e Task 4.
2. **Locação antiga com início anos antes do sistema** — a geração de cobranças nunca cria meses anteriores a `Config.inicio_historico` (fev/2026), senão surge dívida falsa. Teste em Task 3.
3. **Empresa sem acesso direto** — cobrança vencida sem pagamento é "aguardando proprietário", nunca "atrasada", e aparece separada no dashboard. Teste em Task 3 e Task 4.
4. **Troca de inquilino no mesmo imóvel** — cobranças em aberto da locação encerrada continuam nela; a nova locação não herda nada. Teste em Task 3.
5. **Usuário de visualização** — não acessa nenhuma URL de administração (403/redirect), inclusive por URL digitada. Teste em Task 1 e Task 7.

---

## File Structure

```
manage.py
requirements.txt
config/settings.py · urls.py · wsgi.py
locacao/
  models.py          todos os modelos
  dinheiro.py        comissao(), fator_acumulado(), fmt
  cobrancas.py       geração de cobranças, situação, pagamentos/alocação, encerrar
  apuracao.py        resumo do mês e do período (dashboard + comissão)
  indices.py         busca BCB + cálculo/fluxo de reajuste
  tarefas.py         lista de tarefas automática
  importacao.py      leitura do xlsx da planilha
  management/commands/importar_planilha.py
  forms.py · views.py · urls.py · permissoes.py
  templatetags/br.py filtros brl, data_br, mes_br
  templates/locacao/*.html  · templates/base.html · templates/registration/login.html
  static/locacao/app.css
  tests/test_*.py
Dockerfile · docker-compose.yml · scripts/backup.sh · docs/DEPLOY.md · README.md · .gitignore
```

---

### Task 1: Projeto, layout base e acesso

**Files:**
- Create: `manage.py`, `requirements.txt`, `config/settings.py`, `config/urls.py`, `config/wsgi.py`, `locacao/__init__.py`, `locacao/apps.py`, `locacao/permissoes.py`, `locacao/urls.py`, `locacao/views.py`, `locacao/templatetags/__init__.py`, `locacao/templatetags/br.py`, `locacao/templates/base.html`, `locacao/templates/registration/login.html`, `locacao/templates/locacao/placeholder.html`, `locacao/static/locacao/app.css`, `.gitignore`
- Test: `locacao/tests/__init__.py`, `locacao/tests/test_acesso.py`, `locacao/tests/test_br.py`

**Interfaces:**
- Produces: `locacao.permissoes.admin_required` (decorator: exige login e `is_staff`; não-staff logado recebe 403); URL names `login`, `logout`, `dashboard` (`/`), `tarefas` (`/tarefas/`), `saude` (`/saude/` → 200 "ok", sem login); filtros `brl` (`Decimal("1234.5")` → `"R$ 1.234,50"`, `None` → `"—"`), `data_br` (`date(2026,8,1)` → `"01/08/2026"`), `mes_br` (`date(2026,8,1)` → `"ago/2026"`); `base.html` com blocos `title`, `content`, navegação que mostra itens de admin só para staff.

- [ ] **Step 1:** `requirements.txt`: `Django==5.2.18`, `gunicorn==26.2.0`, `whitenoise==6.12.0`, `openpyxl==3.1.5`. Criar venv e instalar.
- [ ] **Step 2: Testes que falham** em `test_acesso.py`:
  - `test_anonimo_redireciona_para_login`: GET `/` → 302 para `/login/?next=/`.
  - `test_visualizacao_ve_dashboard`: usuário `is_staff=False` logado, GET `/` → 200.
  - `test_visualizacao_bloqueada_em_tarefas`: mesmo usuário, GET `/tarefas/` → 403.
  - `test_admin_ve_tarefas`: `is_staff=True`, GET `/tarefas/` → 200.
  - `test_saude_sem_login`: GET `/saude/` → 200, corpo `ok`.
  - `test_nav_esconde_admin_para_visualizacao`: resposta de `/` para não-staff não contém `href="/tarefas/"`.
  Em `test_br.py`: os três filtros com os exemplos acima, mais `brl(Decimal("-10"))` → `"-R$ 10,00"`.
- [ ] **Step 3:** Rodar `python manage.py test locacao` → FAIL.
- [ ] **Step 4:** Implementar settings (constraints globais; whitenoise; `LOGIN_URL="login"`, `LOGIN_REDIRECT_URL="dashboard"`; `SESSION_COOKIE_SECURE`/`CSRF_COOKIE_SECURE`/`SECURE_PROXY_SSL_HEADER` ativos quando `DEBUG=0`; `CSRF_TRUSTED_ORIGINS=["https://"+APP_HOST]` quando definido; `ALLOWED_HOSTS` = `[APP_HOST, "localhost", "127.0.0.1"]`). Login com `django.contrib.auth.views.LoginView`. Views `dashboard` e `tarefas` renderizam `placeholder.html` (serão substituídas nas Tasks 8 e 9). CSS: tokens em `:root`, layout com cabeçalho fixo, navegação horizontal que vira lista em tela estreita, tabelas `.tabela` densas com zebra suave e números alinhados à direita (`font-variant-numeric: tabular-nums`), cartões `.cartao`, badges `.status-*` (paga, parcial, isenta, aberto, atrasada, aguardando), formulários com rótulo acima do campo, botões primário/secundário, mensagens do Django.
- [ ] **Step 5:** Rodar testes → PASS.
- [ ] **Step 6:** Commit `feat: django project, base layout, login and access control`.

---

### Task 2: Modelos e dinheiro

**Files:**
- Create: `locacao/models.py`, `locacao/dinheiro.py`, migration `0001`
- Test: `locacao/tests/test_dinheiro.py`, `locacao/tests/test_models.py`

**Interfaces:**
- Produces (`locacao.dinheiro`):
  - `centavos(v: Decimal) -> Decimal` — quantiza 0.01 HALF_UP.
  - `comissao(valor: Decimal, participacao_pct: Decimal, percentual: Decimal) -> Decimal` = `centavos(valor * participacao_pct/100 * percentual/100)`.
  - `fator_acumulado(variacoes_pct: list[Decimal]) -> Decimal` = produto de `(1 + v/100)`; retorna o fator (ex.: `[1, 1]` → `Decimal("1.0201")`).
  - `primeiro_dia(d: date) -> date`, `somar_meses(d: date, n: int) -> date` (mantém dia 1).
- Produces (`locacao.models`), campos exatos:
  - `Config` (singleton; `Config.get()` cria se não existir): `percentual_comissao` Decimal(5,2) default 5.00; `inicio_historico` Date default 2026-02-01; `mes_corte` Date null (dia 1; null = regra nova desligada); `aviso_termino_dias` int default 90.
  - `Empresa`: `nome`, `cnpj` (blank), `banco` (blank), `acesso_direto` bool default False. `__str__` = nome.
  - `Imovel`: `codigo` unique, `cidade`, `endereco`, `unidade`, `incorporadora`, `imobiliaria`, `empresa` FK null PROTECT, `participacao_pct` Decimal(5,2) default 100, `inscricao_iptu`, `codigo_agua`, `codigo_luz`, `registro_cartorio`, `proprietario_atual`, `outros` Text, `observacoes` Text (todos texto blank). Property `locacao_ativa -> Locacao|None`; property `vago -> bool`. Ordering `codigo`.
  - `Inquilino`: `nome`, `documento`, `nome2`, `documento2`, `contato`, `observacoes` (blank exceto nome).
  - `Locacao`: `imovel` FK PROTECT related_name `locacoes`, `inquilino` FK PROTECT, `corretor`, `inicio` Date, `duracao_meses` int null, `termino` Date null, `dia_vencimento` int (1–31), `indice` choices `IGPM`/`IPCA`/`NENHUM`, `mes_base_reajuste` int null (1–12), `garantia`, `link_contrato` URL blank, `isencao_meses` int default 0, `desconto_pontualidade` Decimal null, `dia_limite_desconto` int null, `observacoes`, `ativa` bool default True, `data_saida` Date null, `situacao_divida` choices `NENHUMA`/`AMIGAVEL`/`JURIDICO`/`ACORDO`/`QUITADA`/`BAIXADA` default `NENHUMA`. Method `valor_em(d: date) -> Decimal|None` (último `ValorAluguel` com `vigente_desde <= d`).
  - `ValorAluguel`: `locacao` FK CASCADE related_name `valores`, `vigente_desde` Date, `valor` Decimal, `motivo` choices `INICIAL`/`REAJUSTE`/`ESCALONAMENTO`/`ACORDO`. Unique (`locacao`, `vigente_desde`).
  - `Cobranca`: `locacao` FK PROTECT related_name `cobrancas`, `competencia` Date (dia 1), `vencimento` Date, `valor_esperado` Decimal, `ajuste` Decimal default 0 (negativo = desconto), `motivo_ajuste`, `quitada` bool default False, `comissao_importada` Decimal null, `observacoes`. Unique (`locacao`, `competencia`). Property `recebido -> Decimal` (soma de `alocacoes.valor`, 0 se nenhuma).
  - `Pagamento`: `locacao` FK PROTECT related_name `pagamentos`, `data` Date null, `valor` Decimal, `observacoes`, `importado` bool default False. Property `nao_alocado -> Decimal`.
  - `Alocacao`: `pagamento` FK CASCADE related_name `alocacoes`, `cobranca` FK PROTECT related_name `alocacoes`, `valor` Decimal.
  - `AnotacaoDivida`: `locacao` FK CASCADE related_name `anotacoes`, `data` Date, `texto` Text.
  - `Reajuste`: `locacao` FK CASCADE related_name `reajustes`, `ano` int, `indice`, `percentual` Decimal(8,4) null, `valor_atual` Decimal, `valor_calculado` Decimal null, `enviado_em` Date null, `decisao` choices `PENDENTE`/`APROVADO`/`RECUSADO` default `PENDENTE`, `valor_aprovado` Decimal null, `decidido_em` Date null, `vigente_desde` Date, `aplicado_em` Date null. Unique (`locacao`, `ano`).
  - `Despesa`: `imovel` FK CASCADE, `tipo` choices `IPTU`/`AGUA`/`CONDOMINIO`/`OUTRA`, `descricao`, `competencia` Date, `vencimento` Date null, `valor` Decimal, `informado_em` Date null.
  - `TarefaManual`: `titulo`, `prazo` Date null, `imovel` FK null SET_NULL, `concluida` bool default False, `criada_em` auto.
  - `ComissaoMes`: `mes` Date unique, `recebida_em` Date null, `observacao`.
  - `RevisaoImportacao`: `aba`, `linha` int, `motivo`, `dados` Text, `resolvida` bool default False.

- [ ] **Step 1: Testes que falham** (`test_dinheiro.py`): `comissao(Decimal("10000"), Decimal("80"), Decimal("5")) == Decimal("400.00")`; `comissao(Decimal("1234.50"), Decimal("100"), Decimal("5")) == Decimal("61.73")`; `fator_acumulado([Decimal("1"), Decimal("1")]) == Decimal("1.0201")`; `fator_acumulado([Decimal("-0.5"), Decimal("0.5")]) == Decimal("0.999975")`; `somar_meses(date(2026,11,1), 3) == date(2027,2,1)`. (`test_models.py`): `valor_em` devolve o valor vigente com escalonamento futuro (100 desde 2026-01-01, 110 desde 2027-01-01 → `valor_em(2026-12-15)==100`, `valor_em(2027-01-01)==110`, `valor_em(2025-12-31) is None`); `Imovel.vago` True sem locação ativa e False com uma; `Cobranca.recebido` soma alocações.
- [ ] **Step 2:** Rodar → FAIL.
- [ ] **Step 3:** Implementar modelos e `dinheiro.py`; `makemigrations`.
- [ ] **Step 4:** Rodar → PASS.
- [ ] **Step 5:** Commit `feat: domain models and money helpers`.

---

### Task 3: Cobranças e pagamentos

**Files:**
- Create: `locacao/cobrancas.py`
- Test: `locacao/tests/test_cobrancas.py`

**Interfaces:**
- Consumes: modelos e `dinheiro` da Task 2.
- Produces (`locacao.cobrancas`):
  - `vencimento_de(locacao, competencia: date) -> date` — dia `dia_vencimento` no mês da competência, limitado ao último dia do mês.
  - `gerar_cobrancas(ate: date, locacao: Locacao|None = None) -> int` — para cada locação ativa (ou só a dada), cria as cobranças faltantes de `max(primeiro_dia(inicio), Config.inicio_historico)` até `primeiro_dia(ate)`. `valor_esperado = 0` nos primeiros `isencao_meses` contados a partir de `primeiro_dia(inicio)`; senão `valor_em(vencimento)` (se `None`, pula o mês). Idempotente. Continua após `termino` enquanto ativa. Retorna quantas criou.
  - `situacao(cobranca, hoje: date) -> str` em `{"PAGA","PARCIAL","ISENTA","ABERTO","ATRASADA","AGUARDANDO"}`: `ISENTA` se `valor_esperado == 0`; `PAGA` se `quitada` ou `recebido >= valor_esperado + ajuste`; `PARCIAL` se `recebido > 0`; `ABERTO` se `hoje <= vencimento`; senão `ATRASADA` se `locacao.imovel.empresa` tem `acesso_direto`, senão `AGUARDANDO` (imóvel sem empresa → `ATRASADA`).
  - `saldo(cobranca) -> Decimal` = `max(0, valor_esperado + ajuste - recebido)`, e `0` se `quitada`.
  - `registrar_pagamento(locacao, data: date|None, valor: Decimal, observacoes: str = "", alocacoes: list[tuple[Cobranca, Decimal]]|None = None) -> Pagamento` — transação. Com `alocacoes`: usa exatamente elas (soma não pode passar de `valor`; senão `ValueError`). Sem: distribui FIFO pelas cobranças não quitadas da locação por competência, pagando `saldo`; se sobrar, gera e paga cobranças futuras mês a mês até `termino` (ou até 24 meses à frente se sem término); sobra final fica não alocada. Cobrança cujo `saldo` chega a 0 recebe `quitada=True`.
  - `encerrar_locacao(locacao, data_saida: date) -> None` — `ativa=False`, grava `data_saida`; cobranças existentes ficam como estão.

- [ ] **Step 1: Testes que falham** (dados inventados, `Config.inicio_historico=2026-02-01`):
  - `test_nao_gera_antes_do_inicio_historico`: locação iniciada 2023-03-01, `gerar_cobrancas(date(2026,4,15))` → competências fev, mar, abr/2026 só.
  - `test_isencao_tres_meses`: início 2026-08-20, `isencao_meses=3`, gerar até 2026-12 → ago, set, out com 0; nov e dez com valor vigente.
  - `test_idempotente`: gerar duas vezes → segunda retorna 0.
  - `test_continua_apos_termino`: término 2026-05-01, ativa → gera junho.
  - `test_aguardando_sem_acesso` / `test_atrasada_com_acesso`: venceu sem pagamento → `AGUARDANDO` vs `ATRASADA`.
  - `test_parcial_e_quitar`: paga 90 de 100 → `PARCIAL`; `quitada=True` → `PAGA`, `saldo==0`.
  - `test_varios_pagamentos_um_mes`: 3 pagamentos de 50, 50 e 50 num mês de 150 → `PAGA`.
  - `test_antecipacao_seis_meses`: aluguel 100, sem cobranças futuras, `registrar_pagamento(valor=600)` em 2026-09-01 com set aberto → set..fev/2027 criadas e quitadas, `nao_alocado == 0`.
  - `test_troca_de_inquilino_nao_herda`: locação A com mar aberto, `encerrar_locacao`, nova locação B no mesmo imóvel → `gerar_cobrancas` não cria nada para A depois da saída; cobrança aberta de A segue em A; B começa limpa.
  - `test_alocacao_explicita_maior_que_valor`: `ValueError`.
- [ ] **Step 2:** Rodar → FAIL.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** Rodar → PASS.
- [ ] **Step 5:** Commit `feat: monthly charges, payments allocation and status`.

---

### Task 4: Apuração (dashboard e comissão)

**Files:**
- Create: `locacao/apuracao.py`
- Test: `locacao/tests/test_apuracao.py`

**Interfaces:**
- Consumes: Tasks 2–3.
- Produces (`locacao.apuracao`):
  - `@dataclass Linha: cobranca: Cobranca, situacao: str, recebido: Decimal, saldo: Decimal`
  - `@dataclass PagamentoLinha: pagamento: Pagamento, competencias: list[date]`
  - `@dataclass Resumo: mes: date, esperada: Decimal, realizada: Decimal, pendente_atrasado: Decimal, pendente_aguardando: Decimal, comissao: Decimal, regra_nova: bool, pendentes: list[Linha], recebidos: list[Linha], pagamentos: list[PagamentoLinha]` com property `pendente_total`.
  - `resumo_mes(mes: date, hoje: date) -> Resumo`:
    - `esperada` = soma `valor_esperado` das cobranças com competência = mês.
    - pendentes = cobranças do mês com `saldo > 0`; somar saldo em `pendente_aguardando` se situação `AGUARDANDO`, senão em `pendente_atrasado` (inclui `ABERTO`/`PARCIAL`/`ATRASADA`).
    - `regra_nova = Config.mes_corte is not None and mes >= Config.mes_corte`.
    - Regra antiga: `realizada` = soma `recebido` das cobranças da competência; `comissao` = soma por cobrança de `comissao_importada` se não nula, senão `comissao(recebido, participacao, percentual)`.
    - Regra nova: `realizada` = soma dos `Pagamento.valor` com `data` no mês; `comissao` = soma de `comissao(pagamento.valor, participacao do imóvel, percentual)` por pagamento; `pagamentos` lista esses pagamentos com as competências alocadas.
  - `resumo_periodo(inicio: date, fim: date, hoje: date) -> list[Resumo]` (um por mês, inclusive) e `totais(resumos) -> dict[str, Decimal]` com chaves `esperada, realizada, pendente_total, comissao`.

- [ ] **Step 1: Testes que falham:**
  - `test_esperada_valor_cheio_com_desconto`: esperado 100, pago 90 e quitada → `esperada==100`, `realizada==90`, `pendente_total==0`.
  - `test_pendente_separado`: uma cobrança vencida em empresa sem acesso (100) e outra com acesso (50) → `pendente_aguardando==100`, `pendente_atrasado==50`.
  - `test_regra_antiga_usa_comissao_importada`: cobrança com `comissao_importada=Decimal("12.34")` → `comissao==12.34`.
  - `test_regra_antiga_participacao`: recebido 1000 em imóvel 80% → `comissao==40.00`.
  - `test_regra_nova_atraso_cai_no_mes_do_pagamento`: corte 2026-09-01; cobrança de set paga em 2026-10-03 → `resumo_mes(set).comissao==0`, `resumo_mes(out).comissao==5.00` (valor 100, 100%).
  - `test_regra_nova_antecipacao_inteira`: corte 2026-09-01; pagamento de 600 em 2026-09-01 cobrindo 6 meses → `resumo_mes(set).realizada==600`, `comissao==30.00`; `resumo_mes(out).comissao==0`.
  - `test_corte_nulo_regra_antiga`.
  - `test_periodo_e_totais`: três meses, totais somam.
- [ ] **Step 2:** Rodar → FAIL. **Step 3:** Implementar. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat: monthly and period summaries with commission cutoff rule`.

---

### Task 5: Índices e reajuste

**Files:**
- Create: `locacao/indices.py`
- Test: `locacao/tests/test_indices.py`

**Interfaces:**
- Produces (`locacao.indices`):
  - `SERIES = {"IGPM": 189, "IPCA": 433}`.
  - `buscar_variacoes(indice: str, inicio: date, fim: date) -> list[Decimal]` — GET `https://api.bcb.gov.br/dados/serie/bcdata.sgs.{n}/dados?formato=json&dataInicial=dd/mm/aaaa&dataFinal=dd/mm/aaaa` com `urllib.request`, timeout 10 s; `valor` vem como string com ponto. Levanta `IndiceIndisponivel` em erro de rede/JSON ou se vierem menos de 12 meses.
  - `janela(mes_base: int, ano: int) -> tuple[date, date]` — 12 meses terminando no mês anterior ao mês-base (mês-base 3/2027 → 2026-03-01 a 2027-02-01).
  - `calcular_reajuste(locacao, ano: int, percentual_manual: Decimal|None = None, buscar=buscar_variacoes) -> Reajuste` — cria/atualiza o `Reajuste` do ano: `vigente_desde = date(ano, mes_base_reajuste, 1)`, `valor_atual = valor_em(vigente_desde)`; `percentual` = manual ou `(fator_acumulado - 1) * 100` quantizado 4 casas; `valor_calculado = centavos(valor_atual * (1 + percentual/100))`.
  - `marcar_enviado(r, data)`, `decidir(r, aprovado: bool, data, valor_aprovado: Decimal|None = None)` (aprovado sem valor usa `valor_calculado`), `aplicar(r, data)` — cria `ValorAluguel(vigente_desde=r.vigente_desde, valor=r.valor_aprovado, motivo="REAJUSTE")`, grava `aplicado_em`, e atualiza `valor_esperado` das cobranças já geradas, não quitadas e sem alocação, com competência ≥ `vigente_desde`.
  - `reajustes_devidos(hoje: date) -> list[tuple[Locacao, int]]` — locações ativas com índice ≠ `NENHUM` e `mes_base_reajuste`, cujo mês-base do ano corrente já chegou (`hoje >= date(ano, mes_base, 1)`), sem `Reajuste` nesse ano e sem `ValorAluguel` de motivo `ESCALONAMENTO` com `vigente_desde` no mesmo ano, e com início há ≥ 12 meses.

- [ ] **Step 1: Testes que falham** (com `buscar` falso, sem rede):
  - `test_janela`: `janela(3, 2027) == (date(2026,3,1), date(2027,2,1))`.
  - `test_calculo_multiplica_fatores`: 12 variações de `1` sobre 1000 → percentual `12.6825`, valor `1126.83`.
  - `test_percentual_manual`: manual `2.5` sobre 1000 → `1025.00`.
  - `test_indice_negativo`: variações somando negativo → valor menor que o atual (o gestor decide no fluxo).
  - `test_fluxo_aplicar`: calcular → enviar → aprovar com valor 1100 → aplicar → `valor_em(vigente_desde)==1100`, cobrança aberta da competência atualizada, cobrança quitada intocada.
  - `test_recusado_nao_aplica`.
  - `test_devidos_ignora_escalonamento`.
  - `test_buscar_erro_levanta` (urlopen falso que levanta `URLError`).
- [ ] **Step 2:** FAIL. **Step 3:** Implementar. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat: index lookup and rent adjustment workflow`.

---

### Task 6: Lista de tarefas

**Files:**
- Create: `locacao/tarefas.py`
- Test: `locacao/tests/test_tarefas.py`

**Interfaces:**
- Produces: `@dataclass Item: tipo: str, titulo: str, detalhe: str, url: str, prioridade: int` (1 = mais urgente) e `listar(hoje: date) -> list[Item]` ordenada por prioridade e depois título. Tipos e prioridade:
  - `ATRASADA` (1): uma por cobrança atrasada; título `"{codigo} — aluguel de {mes} atrasado"`; url da locação.
  - `AGUARDANDO` (1): **um único** item quando houver cobranças aguardando; detalhe lista `codigo mes (N dias)`; url `/aguardando/`.
  - `REAJUSTE_CALCULAR` (2) para `reajustes_devidos`; `REAJUSTE_ENVIAR` (2) calculado e não enviado; `REAJUSTE_DECISAO` (2) enviado e pendente; `REAJUSTE_APLICAR` (2) aprovado e não aplicado.
  - `CONTRATO_VENCIDO` (2) ativa com término < hoje; `CONTRATO_TERMINANDO` (3) término em até `aviso_termino_dias`.
  - `SEM_CONTRATO` (3) ativa sem `link_contrato`.
  - `DESPESA_INFORMAR` (2) despesa sem `informado_em` em imóvel vago.
  - `TRANSFERENCIA` (4) imóvel com `proprietario_atual` preenchido e diferente do nome da empresa.
  - `COMISSAO` (2) mês fechado (anterior ao atual, ≥ `inicio_historico`) com comissão > 0 e sem `ComissaoMes.recebida_em`.
  - `MANUAL` (prazo vencido 1, senão 3) para `TarefaManual` não concluída.
  - `REVISAO` (2) um item se houver `RevisaoImportacao` não resolvida.
  Chama `gerar_cobrancas(hoje)` antes de montar.

- [ ] **Step 1: Testes que falham:** um teste por tipo verificando presença e prioridade; `test_aguardando_agrupado` (3 cobranças → 1 item, detalhe com os 3 códigos); `test_ordem` (prioridade 1 antes de 3).
- [ ] **Step 2:** FAIL. **Step 3:** Implementar. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat: automatic to-do list`.

---

### Task 7: Cadastros (telas de administração)

**Files:**
- Create: `locacao/forms.py`, templates `locacao/lista.html` (genérico), `locacao/form.html` (genérico), `imovel_detalhe.html`, `locacao_detalhe.html`, `config.html`
- Modify: `locacao/views.py`, `locacao/urls.py`, `base.html` (navegação)
- Test: `locacao/tests/test_views_cadastro.py`

**Interfaces:**
- Consumes: modelos, `cobrancas.gerar_cobrancas`, `situacao`, `saldo`.
- Produces URL names: `empresas`, `empresa_nova`, `empresa_editar`; `imoveis`, `imovel_novo`, `imovel_editar`, `imovel` (detalhe); `inquilinos`, `inquilino_novo`, `inquilino_editar`; `locacao_nova` (aceita `?imovel=`), `locacao_editar`, `locacao` (detalhe), `valor_novo` (para uma locação), `locacao_encerrar`; `despesas`, `despesa_nova`, `despesa_editar`, `despesa_informada` (POST marca hoje); `tarefa_nova`, `tarefa_concluir` (POST); `config`.
- Todas com `admin_required`. Formulários POST com CSRF; após salvar, redireciona e mostra mensagem de sucesso.
- `imovel` detalhe: dados do imóvel agrupados (Identificação, Contas e registros, Outros) e linha do tempo de locações (mais recente primeiro) com inquilino, período, situação e saldo em aberto de cada uma.
- `locacao` detalhe: cabeçalho (imóvel, inquilino, período, vencimento, índice, link do contrato), valores vigentes e futuros, tabela de cobranças (competência, vencimento, esperado, ajuste, recebido, situação com badge, ações), pagamentos, reajustes, bloco Dívida (situação + anotações) quando houver saldo em aberto.
- Listas: busca simples por texto (`?q=`) em código/nome; imóveis mostram status Alugado/Vago e inquilino atual.

- [ ] **Step 1: Testes que falham:** criar imóvel por POST e ver na lista; criar locação com valor inicial (o formulário de locação nova inclui o campo `valor_inicial` que cria o `ValorAluguel INICIAL` em `inicio`); encerrar locação via POST deixa `ativa=False`; `test_visualizacao_403_em_todas` percorre todas as URL names desta task (com ids de objetos criados) e espera 403; detalhe da locação lista as cobranças geradas.
- [ ] **Step 2:** FAIL. **Step 3:** Implementar (CBVs ou funções; templates genéricos de lista/form). **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat: admin screens for registries`.

---

### Task 8: Fluxos (pagamento, cobrança, dívida, reajuste, comissão, revisão)

**Files:**
- Create: templates `pagamento_form.html`, `cobranca_form.html`, `reajustes.html`, `comissoes.html`, `aguardando.html`, `revisao.html`, `tarefas.html`
- Modify: `locacao/views.py`, `locacao/urls.py`, `locacao/forms.py`
- Test: `locacao/tests/test_views_fluxos.py`

**Interfaces:**
- Produces URL names: `pagamento_novo` (por locação): campos data, valor, observações e, opcionalmente, uma linha por cobrança não quitada para alocar manualmente; vazio = automático (FIFO). `cobranca_editar`: ajuste, motivo, quitada, observações. `anotacao_nova` (dívida) e o campo `situacao_divida` na própria tela. `reajustes` (lista do ano, com botões calcular / enviado / aprovar com valor / recusar / aplicar; campo de percentual manual quando o BCB falhar, com mensagem "Não foi possível obter o índice do Banco Central. Informe o percentual manualmente."). `comissoes` (por mês desde `inicio_historico`: comissão apurada, regra usada, recebida em; POST marca recebida com data). `aguardando` (cobranças aguardando proprietário, agrupadas por imóvel, com dias de espera; ação "registrar pagamento"). `revisao` (itens da importação; POST marca resolvido). `tarefas` passa a renderizar `tarefas.listar(hoje)` em grupos por prioridade, cada item com link; formulário rápido de tarefa manual no topo; é a página inicial do admin após login (`LOGIN_REDIRECT_URL` vai para `tarefas` se staff — usar view de redirecionamento `inicio` em `/inicio/`).

- [ ] **Step 1: Testes que falham:** pagamento automático de 300 quita 3 meses; pagamento com alocação manual; marcar cobrança quitada; reajuste com `buscar` falso (monkeypatch `locacao.indices.buscar_variacoes`) até aplicado; reajuste com falha mostra campo manual; marcar comissão recebida faz o item `COMISSAO` sumir de `/tarefas/`; após login, staff cai em `/tarefas/` e visualização em `/`.
- [ ] **Step 2:** FAIL. **Step 3:** Implementar. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat: payment, adjustment, debt, commission and review workflows`.

---

### Task 9: Dashboard e visão do período

**Files:**
- Create: templates `dashboard.html`, `periodo.html`
- Modify: `locacao/views.py`, `locacao/urls.py`
- Test: `locacao/tests/test_views_dashboard.py`

**Interfaces:**
- Consumes: `apuracao.resumo_mes`, `resumo_periodo`, `totais`.
- `dashboard` (`/?mes=2026-08`, padrão = mês atual): seletor de mês com anterior/próximo; quatro cartões (Receita esperada, Receita realizada, Pendente — com "atrasado X · aguardando proprietário Y" abaixo, Comissão — com a regra usada, ex.: "por data de pagamento"); tabelas Pendentes e Recebidos (código, inquilino, vencimento, esperado, recebido, situação); na regra nova, tabela Pagamentos do mês (data, imóvel, valor, referente a). Para visualização: mesma página, sem links de ação.
- `periodo` (`/periodo/?ano=2026&semestre=2`, sem `semestre` = ano todo): tabela mês a mês com as quatro colunas e linha de total; link a partir do dashboard. Acessível para visualização.

- [ ] **Step 1: Testes que falham:** dashboard mostra os valores formatados do `Resumo` de um cenário inventado; mês inválido na query → mês atual sem erro; período soma; visualização acessa ambos e não vê links `/locacoes/`.
- [ ] **Step 2:** FAIL. **Step 3:** Implementar. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat: dashboard and period view`.

---

### Task 10: Importação da planilha

**Files:**
- Create: `locacao/importacao.py`, `locacao/management/__init__.py`, `locacao/management/commands/__init__.py`, `locacao/management/commands/importar_planilha.py`
- Test: `locacao/tests/test_importacao.py` (gera um `.xlsx` inventado em `tempfile` com openpyxl)

**Interfaces:**
- Produces: `importar(caminho: str, forcar: bool = False) -> dict[str, int]` (contagens por entidade + `revisao`). Recusa (`RuntimeError`) se já existir algum `Pagamento` não importado, a menos que `forcar`. Antes de importar apaga tudo o que foi importado (todas as tabelas de domínio) — só permitido sob a condição acima. Comando: `python manage.py importar_planilha caminho.xlsx [--forcar]`.
- Mapeamento (cabeçalhos na linha 1, localizar colunas pelo **nome do cabeçalho**, não pela posição; comparar sem acento e sem caixa):
  - **Imoveis**: `ID Imovel`→codigo, `Cidade`, `Endereco`, `Unidade`, `Inscricao`→inscricao_iptu (número vira texto sem `.0`), `Incorporadora`, `Imobiliaria`, `Observacoes`, `Energia`→codigo_luz; colunas sem cabeçalho à direita de `IPTU` são concatenadas em `outros` ("IPTU: …"). Empresa: `get_or_create(nome=Incorporadora)`; se vazio, a empresa já criada que tem mais imóveis na mesma cidade; se nenhuma, `RevisaoImportacao`.
  - **Contratos**: `ID Imovel`, `Inquilino`, `Valor Aluguel`, `Vencimento`→dia, `Banco Pagamento`→`Empresa.banco` (só se vazio na empresa), `Data Inicio`, `Duracao`, `Data Termino`, `Indice` (`IGP-M`→IGPM, `IPCA`, vazio→NENHUM), `Data Proximo Reajuste`→`mes_base_reajuste` = mês dessa data, `Link Contrato PDF`, `Observações`. Valor vazio → locação sem valor + revisão. Observação exatamente igual a `0.8` → `Imovel.participacao_pct = 80`. Linha "Total" ignorada.
  - **Inquilinos**: por `ID Imovel` + `Inquilino (Resumo)`, preencher `nome` (Nome Locatario 1), `documento`, `nome2`, `documento2`, `contato`, `observacoes`; `Tipo de Garantia`→`Locacao.garantia`.
  - **Lancamentos**: cada linha com `ID Imovel` e `Mes/Ano Referencia` vira `Cobranca` (competência a partir do texto `mmm./aaaa` em português ou de data) na locação do imóvel cujo inquilino tem o mesmo nome (`Inquilino`); se não houver, cria `Inquilino` + `Locacao` encerrada (`ativa=False`, início = primeira competência) para esse nome — é assim que inquilinos antigos aparecem. `valor_esperado = Valor Aluguel` (vazio → valor do contrato), `comissao_importada = Comissao (5%)` quando preenchida. `Valor Recebido > 0` → `Pagamento(importado=True, data=Data Pagamento ou None)` alocado à cobrança. Status `Recebido` → `quitada=True`; `Parcial` → não quitada. Observações copiadas. Linha sem `ID Imovel` ou sem referência → `RevisaoImportacao` com todos os valores da linha.
  - **Outros** e demais abas: não importadas automaticamente; registrar um `RevisaoImportacao` dizendo "conferir aba Outros manualmente".
  - Ao final: `Config.inicio_historico` = menor competência importada; `gerar_cobrancas` **não** é chamado para meses já presentes.

- [ ] **Step 1: Testes que falham:** planilha inventada com 2 imóveis (um `0.8`), 2 contratos, 1 inquilino trocado no meio (lançamentos com dois nomes no mesmo imóvel), uma linha sem imóvel → asserts: 2 imóveis, participação 80 no correto, 3 locações (1 encerrada), comissão importada preservada, pagamento sem data importado com `data=None`, 1+ revisão, `importar` de novo funciona; após criar um `Pagamento` manual, `importar` sem `forcar` levanta `RuntimeError`.
- [ ] **Step 2:** FAIL. **Step 3:** Implementar. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat: spreadsheet import with review queue`.

---

### Task 11: Empacotamento, deploy e backup

**Files:**
- Create: `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `scripts/backup.sh`, `docs/DEPLOY.md`, `README.md`
- Test: `docker build` local e `docker compose config`

**Interfaces:**
- `Dockerfile`: `python:3.12-slim`, usuário não-root, `pip install -r requirements.txt`, `collectstatic`, CMD `gunicorn config.wsgi -w 2 -b 0.0.0.0:8000 --max-requests 500`. Entrypoint roda `migrate --noinput`.
- `docker-compose.yml`: serviço `gestao`, `restart: unless-stopped`, `env_file: .env`, volume `./dados:/data` com `DB_PATH=/data/db.sqlite3`, `mem_limit: 256m`, `cpus: 0.5`, healthcheck em `/saude/`, rede externa `${TRAEFIK_NETWORK}`, labels Traefik: `traefik.enable=true`, `traefik.http.routers.gestao.rule=Host(\`${APP_HOST}\`)`, `entrypoints=${TRAEFIK_ENTRYPOINT}`, `tls.certresolver=${TRAEFIK_CERTRESOLVER}`, `services.gestao.loadbalancer.server.port=8000`. Sem portas publicadas no host.
- `scripts/backup.sh`: `sqlite3`-safe backup via `docker compose exec -T gestao python -c` usando `sqlite3.Connection.backup` para `/data/backup.sqlite3`; criptografa com `age -r "$AGE_RECIPIENT"`; envia com `curl` para `https://api.telegram.org/bot$TG_TOKEN/sendDocument` (`chat_id=$TG_CHAT`); apaga os arquivos temporários; sai ≠0 em qualquer falha (`set -euo pipefail`). Variáveis vêm de `.env` (fora do git).
- `docs/DEPLOY.md`: passos (clonar, `.env`, `docker compose up -d`, `createsuperuser`, criar usuário de visualização, importar, cron semanal do backup, como restaurar com `age -d`), sem hostname real.
- `README.md`: o que é, como rodar local (`python manage.py runserver`), como testar.

- [ ] **Step 1:** `docker build -t gestao .` → sucesso; `docker run` com `DJANGO_DEBUG=1` e GET `/saude/` → `ok`.
- [ ] **Step 2:** `python manage.py check --deploy` com `DJANGO_DEBUG=0` e variáveis de exemplo → sem erros (warnings aceitáveis listados no DEPLOY.md).
- [ ] **Step 3:** Commit `chore: docker packaging, traefik deploy and encrypted backup`.
