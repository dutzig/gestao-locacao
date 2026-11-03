# Gestão de Locação — Design

Status: rascunho para revisão · 2026-11-02

## 1. Intenção

Um gestor administra algumas dezenas de imóveis comerciais e residenciais em duas cidades de um
proprietário, recebendo 5% de comissão. Hoje tudo vive numa planilha Google com
abas Dashboard, Lançamentos, Contratos, Inquilinos, Imóveis e Outros. A planilha
falha em três pontos que o sistema precisa resolver:

1. **Informação que depende de terceiros fica invisível.** Os imóveis estão em
   duas empresas. A conta de uma o gestor confere direto no banco; a da
   outra depende do proprietário informar, e a reunião mensal em que isso
   acontecia parou há 3 meses. Corretores parceiros fecham contratos sem avisar;
   o gestor descobre a troca de inquilino por um pagamento não reconhecido e
   espera semanas pelo contrato.
2. **O histórico é do imóvel, não do inquilino.** Quando um inquilino sai devendo
   e entra outro com isenção para reparos, a planilha mistura os dois.
3. **Ninguém é lembrado de nada.** Reajustes anuais não foram feitos desde que a
   reunião parou; datas de reajuste estão como texto; despesas de imóvel vago e
   IPTU ficam soltos numa aba "Outros".

O sistema substitui a planilha. A meta do gestor é aposentá-la no primeiro mês.

## 2. Objetivos

- Registrar cada **locação** (inquilino + imóvel + período) com histórico próprio:
  cobranças, pagamentos, isenções, descontos, dívida.
- Mostrar para cada mês o que era esperado, o que entrou, o que está pendente e a
  comissão — o mesmo dashboard da planilha.
- Separar "inquilino não pagou" de "ainda não sei se pagou" (empresa sem acesso).
- Uma **lista de tarefas** na página do administrador que mostra o que precisa de
  ação, sem e-mail nem notificação.
- Calcular reajustes pelo índice oficial e acompanhar a aprovação do proprietário.
- Importar o histórico da planilha (desde fev/2026).
- Aceitar novos campos e regras ao longo do uso sem refazer o sistema.

## 3. Não-objetivos

- Palestras (abas Negociações, PalestrasOK, Data Evento) — ficam fora.
- Repasse da parte do sócio (20%) — problema do proprietário.
- Acompanhar se o proprietário pagou as despesas de imóvel vago — problema dele.
- Cobrança automática (boleto/Pix), integração bancária, portal do inquilino,
  anúncios, chat, assinatura de contrato.
- Envio de mensagens a inquilinos ou proprietário.
- Guardar PDFs de contrato (ficam no Dropbox; o sistema guarda o link).
- Sincronização com a planilha durante o período em paralelo.

## 4. Anti-objetivos (falha mesmo que funcione)

- **Dado de inquilino no GitHub.** CPF, CNPJ, telefone, nomes e valores nunca
  entram no repositório — nem em fixture, teste, exemplo, commit ou neste
  documento. O repositório é público e só contém código.
- Comissão errada. A planilha já errou uma vez (março calculado sobre 100% em vez
  de 80%). Toda conta de dinheiro tem teste.
- Interface carregada: o gestor quer um sistema sóbrio.
- Atrapalhar o n8n que roda na mesma VPS.

## 5. Usuários e acesso

- **Administrador** (o gestor): vê e altera tudo.
- **Visualização** (proprietário, liberado ocasionalmente): vê só o dashboard,
  incluindo a visão anual/semestral. Não vê cadastros, tarefas nem documentos
  pessoais de inquilinos.
- Página web com login e senha. HTTPS obrigatório (há CPF/CNPJ trafegando).

## 6. Domínio

### 6.1 Empresa
As duas empresas do proprietário em nome das quais os imóveis estão. Campos:
nome, CNPJ, banco, **acesso direto à conta** (sim/não). Quando o gestor receber
acesso à segunda conta, basta marcar "sim" — nada mais muda.

### 6.2 Imóvel
Código (ex.: UF-001, padrão já usado na planilha), cidade, endereço, unidade,
incorporadora, imobiliária/corretor de origem, empresa, **participação do
proprietário** (% — 100 por padrão, 80 nos imóveis com sócio), inscrição do IPTU,
código da água, código da luz, registro no cartório, **proprietário atual**
(texto; alguns imóveis ainda não foram transferidos), campo livre "outros",
observações. Status (alugado/vago) é derivado de existir locação ativa.

### 6.3 Inquilino
Locatário 1 (nome/razão social, CPF/CNPJ), locatário 2 / representante (nome,
CPF/CNPJ), contato (opcional — o gestor nem sempre tem), observações. Um
inquilino pode ter mais de uma locação ao longo do tempo.

### 6.4 Locação (o contrato)
Imóvel, inquilino, corretor que fechou, data de início, duração (meses), data de
término, dia de vencimento, índice de reajuste (IGP-M, IPCA ou nenhum), mês-base
do reajuste, garantia (texto), link do contrato (Dropbox), observações, situação
(ativa / encerrada) e data de saída.

**Valor vigente com histórico.** O valor do aluguel é uma sequência de valores
datados (inicial, reajuste, escalonamento previsto em contrato, acordo). O
escalonamento por ano do contrato (ex.: 100 → 110 → 115) é cadastrado
de antemão como valores futuros.

**Desconto por pontualidade.** Valor do desconto e dia-limite (ex.: R$ 100 se
pago até o dia 5). Quando o pagamento entra dentro do prazo, o valor esperado é o
valor com desconto.

**Isenção.** Meses de isenção (ex.: 3 primeiros meses) cadastrados na locação;
as cobranças desses meses nascem com valor zero e motivo "isenção".

### 6.5 Cobrança (uma por locação por mês)
Competência (mês/ano de referência), vencimento, valor esperado, ajustes do mês
(desconto por reparo, acordo — valor e motivo), observações, e a marca
**quitada** (ver seção 12).

### 6.5a Pagamento
Cada dinheiro que entra é um pagamento: **data em que entrou**, valor, locação,
observação, e a quais cobranças (meses de referência) ele se refere, com quanto
para cada uma. Um mês pode receber vários pagamentos (há casos pagos em 3
parcelas); um pagamento pode cobrir vários meses (antecipação de 6 meses ou do
contrato inteiro), e nesse caso todas as cobranças cobertas ficam quitadas.
O "recebido" de uma cobrança é a soma do que foi alocado a ela.

Situação, calculada:
- **Paga** — recebido ≥ esperado.
- **Parcial** — recebido > 0 e < esperado.
- **Isenta** — esperado = 0.
- **Em aberto** — nada recebido, ainda não venceu.
- **Atrasada** — venceu, nada recebido, empresa com acesso direto.
- **Aguardando proprietário** — venceu, nada registrado, empresa **sem** acesso
  direto. Nunca é tratada como inadimplência.

Ao encerrar uma locação, as cobranças não pagas continuam ligadas a ela. O
inquilino seguinte começa sem herdar nada.

### 6.6 Dívida
Visão das cobranças não pagas de locações encerradas (e das ativas, se o gestor
quiser), agrupadas por locação, com **situação da cobrança da dívida**: amigável,
jurídico, acordo, quitada, baixada — e um histórico de anotações datadas.

### 6.7 Comissão
`comissão = valor × participação do proprietário × 5%`.

**Em que mês a comissão cai** depende de um **mês de corte** configurável pelo
gestor (enquanto não configurado, nenhum mês usa a regra nova):
- Meses **antes do corte**: regra antiga, igual à planilha — comissão pela
  competência da cobrança. Meses importados mantêm exatamente a comissão que a
  planilha registrou, sem recálculo.
- Meses **a partir do corte**: comissão pela **data do pagamento**. Atraso que
  passa da virada cai no mês em que entrou; antecipação entra inteira no mês em
  que entrou.

**Controle da comissão:** uma tela lista a comissão apurada de cada mês e o
gestor marca "recebida" (com data) quando o proprietário pagar. Mês fechado com
comissão não marcada vira tarefa.
O percentual de 5% é configurável. Exemplos que devem passar em teste:
recebido 10.000 com participação 80% → 400,00; recebido 1.234,50 com 100% → 61,73
(arredondamento meio-para-cima em centavos).

### 6.8 Reajuste
- No mês-base de cada ano o sistema cria a tarefa "calcular reajuste".
- Índice acumulado de 12 meses obtido do Banco Central (SGS: IGP-M série 189,
  IPCA série 433), **multiplicando os fatores mensais** — nunca somando.
- Fluxo: calculado → enviado ao proprietário → aprovado (com o valor aprovado,
  que pode diferir do calculado) ou recusado → aplicado, criando um novo valor
  vigente a partir da data definida.
- Cada etapa registra data. Tarefa permanece aberta até "aplicado" ou "recusado".
- Se o BCB estiver fora do ar, o gestor pode digitar o percentual à mão.

### 6.9 Despesas de imóvel vago
IPTU, taxa mínima de água, condomínio e outras. Campos: imóvel, tipo,
competência, vencimento, valor, data em que foi **informado ao proprietário**.
O ciclo termina quando o gestor informa; não se acompanha o pagamento.

### 6.10 Lista de tarefas (página do administrador)
Gerada a cada visita a partir dos dados, mais tarefas manuais. Itens automáticos:
- Cobrança atrasada (empresa com acesso).
- Cobranças aguardando proprietário — agrupadas num único item com a lista e
  há quantos dias aguardam.
- Reajuste a calcular / aguardando aprovação / aprovado e não aplicado.
- Contrato terminando (janela configurável).
- Locação ativa sem link do contrato.
- Imóvel vago: despesas do mês não informadas ao proprietário.
- Imóvel com proprietário atual diferente da empresa (transferência pendente).
- Tarefa manual: título, prazo opcional, imóvel opcional (ex.: "cobrar contrato
  novo do corretor").

Cada item leva direto à tela onde se resolve.

### 6.11 Dashboard
Seletor de mês. Cartões: receita esperada (por referência), receita realizada,
total pendente (por referência, separando "atrasado" de "aguardando
proprietário"), comissão. Receita realizada segue a mesma regra de corte da
comissão (antes: por referência; a partir do corte: pela data do pagamento).
Cada pagamento listado mostra a data em que entrou e o mês a que se refere. Tabelas: pendentes
e recebidos do mês. Visão do ano e do semestre: os mesmos quatro números mês a
mês e o total do período. O histórico começa em fev/2026.

## 7. Importação da planilha

- Comando que lê o `.xlsx` exportado **na própria VPS** (o arquivo nunca entra
  no git) e grava no banco.
- Abas importadas: Imóveis, Inquilinos, Contratos, Lançamentos e os campos
  úteis de Outros (códigos de água/luz, inscrições).
- Linhas que não se encaixam (lançamento sem imóvel, valor que diverge do
  contrato, inquilino diferente do cadastrado) não são descartadas nem
  adivinhadas: entram numa lista "revisar importação" que o gestor resolve à mão.
- Histórico importado como está: valores recebidos e comissões da planilha são
  gravados sem recálculo ("o que passou já foi"). Lançamentos sem data de
  pagamento importam sem data.
- Rodar a importação duas vezes não duplica dados (apaga e reimporta, enquanto o
  sistema não estiver em uso; depois disso o comando se recusa a rodar).

## 8. Restrições técnicas

- VPS Hostinger pequena (1 vCPU, ~2,2 GB livres) que roda n8n como prioridade.
  O sistema precisa caber em ~100 MB de RAM e ficar ocioso sem uso.
- Portas 80/443 são do n8n (docker). As já usadas pelo nginx: 8443–8445.
- Python 3.12 na VPS.
- Dados em SQLite na VPS. Backup diário do banco, criptografado, fora do GitHub.
- Repositório público no GitHub com só o código. `.gitignore` cobre banco,
  planilhas, backups e `.env`. Testes usam dados inventados.

## 9. Decisões de construção (escolha do construtor, informadas ao gestor)

- **Django 5 + SQLite**, páginas renderizadas no servidor, CSS próprio sem build,
  JavaScript mínimo. Motivo: login, permissões, formulários, migrações e
  proteção CSRF já vêm prontos; nada para compilar; leve.
- gunicorn com 2 workers num container Docker leve, atrás do Traefik existente.
- Valores monetários em `Decimal`, nunca `float`. Datas e moeda no formato
  brasileiro. Fuso America/Sao_Paulo.
- Visual sóbrio: fundo claro neutro, uma cor de destaque, tipografia do sistema,
  tabelas densas e legíveis, cores de status com texto (não só cor), contraste
  AA, funciona no celular.

## 10. Deixado ao construtor

Nomes de tabelas e telas, layout exato, ordem das tarefas, paginação, mensagens.

## 11. Respostas do gestor (após revisão)

- **Empresas:** duas, cada uma com um banco. Uma tem acesso direto do gestor à
  conta, a outra não. A empresa de cada imóvel vem da coluna Incorporadora da
  planilha; se vazia, da cidade (cada empresa concentra uma cidade, com exceções
  que a coluna já resolve).
- **Esperado × recebido:** o proprietário dá descontos pontuais que o gestor só
  descobre depois. Receita esperada = valor vigente do contrato. Receita
  realizada e comissão = valor efetivamente lançado.
- **Multa/juros:** ninguém paga. Não existe multa no sistema.
- **Escalonamento × índice:** sem regra automática. Valores futuros do
  escalonamento ficam editáveis; o reajuste por índice só é sugerido quando a
  locação tem índice e não tem valor futuro já cadastrado para o período.
- **IPTU dividido entre inquilinos:** fora do sistema.
- **Lançamentos sem imóvel na planilha:** vão para "revisar importação"; o
  gestor completa à mão.
- **Endereço:** subdomínio do hostname gratuito que a Hostinger dá à VPS, com
  HTTPS pelo proxy Traefik que já existe lá (certificado Let's Encrypt
  automático). O sistema roda num container próprio com rótulos do Traefik; a
  configuração do n8n não é alterada. O hostname fica em variável de ambiente,
  fora do repositório.
- **Backup:** semanal. Arquivo do banco criptografado com senha (que fica com o
  gestor, não na VPS) e enviado ao Telegram dele por um bot já existente.

## 12. Decisões do construtor (para o gestor conferir)

- **Recebido menor que o esperado:** a cobrança fica "Parcial" até o gestor
  marcar "quitada" (a diferença vira desconto concedido, com motivo opcional).
  Status "Recebido" da planilha importa como quitada; "Parcial" como parcial.
  "Total pendente" = esperado das cobranças não quitadas menos o que já entrou
  nelas.
- **Desconto por pontualidade:** é um tipo de desconto concedido; o esperado
  continua sendo o valor cheio (a planilha faz assim: descontos não aparecem
  como pendência).
- **Pagamento acima do esperado:** aceito, conta no recebido e na comissão.
- **Prorrogação tácita:** cobranças continuam sendo geradas após o término
  enquanto a locação estiver ativa; a tarefa "contrato vencido" fica aberta.
- **Renovação com o mesmo inquilino:** estende a locação atual (novo término).
- **Entrada/saída no meio do mês:** sem pró-rata automático; o gestor ajusta o
  valor da cobrança do mês.
- **Janela do índice:** 12 meses terminando no mês anterior ao mês-base.
- **Contrato terminando:** aviso 90 dias antes.
- **Novos campos/regras:** acrescentados por desenvolvimento (migração), não pela
  interface.
- **Mapa da planilha:** a importação conhece as colunas de cada aba; o arquivo
  fica fora do repositório e os testes da importação usam planilha inventada.
