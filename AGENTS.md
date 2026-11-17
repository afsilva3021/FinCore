# AGENTS.md — FinCore

> Instruções operacionais para o Codex. Este arquivo complementa o `README.md`: o README apresenta o projeto; este arquivo define como implementá-lo e validá-lo.

## 1. Missão e escopo

Construir o **FinCore**, um ERP para comércio (venda de produtos) que nasceu como aplicação de gestão financeira e contábil, com interface em português (pt-BR), arquitetura modular, rastreabilidade e testes automatizados. O objetivo é obter um **sistema funcional, executável localmente e verificável**, implementado por incrementos completos — não apenas telas, stubs ou uma estrutura vazia.

O núcleo financeiro e contábil (fases 1 a 7) está implementado. A transformação em ERP segue as fases 8 a 13 de `docs/erp-roadmap.md`, que registra as decisões aprovadas e o modelo de dados previsto; leia-o antes de mexer em produtos, estoque, compras, vendas, CRM ou fiscal.

Leia o `README.md` e os arquivos em `docs/` antes de trabalhar. O README atual lista funcionalidades desejadas; **não presuma que elas já estão implementadas**. Se requisitos estiverem incompletos, documente premissas em `docs/decisions.md`, faça a escolha reversível mais segura e prossiga. Não invente obrigações regulatórias, contratos de APIs bancárias, credenciais ou layouts CNAB de bancos específicos.

## 2. Stack obrigatória

- **Linguagem:** Python, versão estável compatível com as dependências escolhidas; fixar a versão em `.python-version` e `pyproject.toml`.
- **Dependências e execução:** `uv`, com lockfile (`uv.lock`).
- **Servidor HTTP / framework:** Robyn.
- **Banco de dados:** PostgreSQL via Docker Compose.
- **ORM:** Tortoise ORM, com solução de migrations compatível com as versões **efetivamente instaladas**. Verificar os comandos disponíveis antes de documentá-los ou executá-los.
- **Interface:** HTML, CSS, JavaScript e Bootstrap, com templates renderizados no servidor quando adequado. Não introduzir React, Vue, Django, FastAPI, Flask ou outros frameworks sem justificativa aprovada.
- **Testes:** pytest e httpx; testes de persistência usam PostgreSQL de teste isolado.
- **Qualidade:** tipagem Python, lint/format configurados e CI, preferencialmente com Ruff.

Prefira bibliotecas mantidas e necessárias. Registre decisões e alterações de dependência. Não adicione infraestrutura distribuída (filas, microsserviços etc.) sem necessidade demonstrada.

## 3. Diretrizes de arquitetura

Adotar **monólito modular**, com separação entre HTTP, aplicação, domínio e infraestrutura. A estrutura abaixo é referência; pode ser expandida sem quebrar as responsabilidades:

```text
FinCore/
├── AGENTS.md
├── README.md
├── pyproject.toml
├── uv.lock
├── .env.example
├── .python-version
├── compose.yaml
├── src/
│   └── fincore/
│       ├── app.py              # inicialização Robyn e registro de rotas
│       ├── presentation/     # rotas Robyn, validação HTTP, views/templates
│       ├── application/      # casos de uso, DTOs, interfaces e transações
│       ├── domain/           # regras, entidades e valores financeiros
│       ├── infrastructure/   # ORM, repositórios, adapters externos
│       └── config/           # configurações e inicialização
├── templates/
├── static/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
└── docs/
    ├── requirements.md
    ├── decisions.md
    └── progress.md
```

Esta árvore é uma referência de responsabilidades; confira a estrutura real do repositório antes de criar arquivos. O entrypoint existente fica em `src/fincore/app.py`, não em `app.py` na raiz.

- Rotas HTTP não devem conter regras contábeis ou SQL direto.
- Rotas ficam em `src/fincore/presentation/routes/<área>.py` e usam `@login_required`/`@api_login_required`; leituras vêm de `application/queries.py`. Regras puras ficam em `domain/`. `tests/unit/test_architecture.py` falha se a apresentação consultar o ORM ou se o domínio importar framework/ORM.
- Casos de uso coordenam validações, autorização, persistência e chamadas externas.
- Entidades e políticas de domínio não devem depender do framework web.
- Camada de infraestrutura encapsula detalhes do Tortoise ORM e integrações bancárias.
- Onde a infraestrutura não garantir atomicidade, não simular transação distribuída: registrar limitações e usar padrões seguros, como outbox e idempotência, quando necessários.

## 4. Modelo de negócio e invariantes obrigatórias

### 4.1 Dinheiro e consistência

- Nunca usar `float` para dinheiro. Usar `Decimal` na aplicação e `NUMERIC(p,s)` no PostgreSQL, com escala definida por moeda; valores persistidos devem ter arredondamento explícito.
- Valores monetários sempre associados à moeda (começar por BRL). Timestamps em UTC no banco, com exibição em horário configurável.
- Cada operação financeira que altera saldos deve ser atômica, validada e auditável.
- Evitar exclusão física de lançamentos financeiros efetivados; utilizar estorno por novos lançamentos compensatórios e preservar histórico.
- **Idempotência** em importações, webhooks e operações de movimentação repetíveis. Implementar chaves únicas e resposta consistente a reenvios.
- Bloquear transações concorrentes que possam gerar saldo ou estado inconsistente, usando mecanismos apropriados do PostgreSQL.

### 4.2 Contabilidade de partidas dobradas

- Criar plano de contas e lançamentos com cabeçalho (`journal_entry`) e linhas (`journal_entry_line`).
- Cada lançamento efetivado deve ter **ao menos duas linhas** e soma de débitos igual à soma de créditos, na mesma moeda. Não admitir valores negativos em campos de débito/crédito.
- Separar rascunho, efetivação e estorno. Depois de efetivado, o lançamento é imutável; ajustes ocorrem por lançamentos de correção/estorno com referência cruzada.
- Saldo contábil é derivado dos lançamentos efetivados; não confiar em campo de saldo editável como única fonte da verdade.
- Definir regras para contas de ativo, passivo, patrimônio líquido, receita e despesa e documentá-las em `docs/accounting.md`.
- Transferência entre contas deve produzir movimentos consistentes e rastreáveis, sem criar receita ou despesa fictícia.

### 4.3 Gestão financeira e títulos

- Usuários autenticados, com autorização por perfil/escopo. Dados de organizações/usuários diferentes não podem se misturar.
- Contas financeiras, categorias, favorecidos, receitas, despesas, transferências, saldos, extratos e relatórios.
- **Transação/movimentação e título são conceitos distintos.** O extrato registra movimento financeiro efetivo ou previsto na rotina de transações; contas a pagar/receber registram títulos próprios. Não implemente títulos como filtros ou aliases de `FinancialTransaction`. Cadastrar título não movimenta saldo nem aparece no extrato. Uma baixa de título cria transação vinculada, de forma atômica e auditável.
- Modelo de título deve suportar: tipo pagar/receber; descrição; contato (fornecedor, cliente ou outro); documento (número, tipo e série); categoria financeira; centro de custo/projeto; valor original e moeda; emissão, vencimento e previsão de recebimento; forma/condição de pagamento; conta financeira; parcelas; observações e referência de anexo.
- Campos de baixa: valor aplicado, data, desconto, juros, multa, tarifas, valor líquido, conta, forma e referência de comprovante. Guardar saldo em aberto como valor derivado do valor original menos valores aplicados; não confiar em campo editável.
- Campos específicos a pagar: beneficiário, código de barras/linha digitável, dados bancários/chave Pix e referência de aprovador quando houver fluxo de aprovação. A receber: pedido/contrato/venda, meio e data de envio da cobrança, previsão de recebimento e link/identificador da cobrança. Campos de anexos e cobrança são referências até existir armazenamento/provedor definido.
- Parcelamento cria títulos independentes ligados por grupo, preserva soma exata em `Decimal` e define vencimentos previsíveis. Baixas parciais atualizam saldo/situação sem duplicar movimentos em reenvios (chave idempotente).
- Situações permitidas: em aberto, parcial, pago/recebido e cancelado; vencido é condição calculada a partir da data de vencimento e saldo aberto. Título cancelado não recebe baixa; título com baixa não é cancelado nem excluído fisicamente.
- Separar data de competência, vencimento e liquidação; não confundir previsão com saldo realizado.
- Definir estados e transições permitidos para títulos, pagamentos, conciliações e lançamentos.
- Registrar usuário, data e origem das ações relevantes (trilha de auditoria), sem persistir segredos ou dados sensíveis em logs.

### 4.4 Conciliação e integrações

- **OFX:** importar arquivos de exemplo, validar formato, evitar duplicidade e manter origem/identificador externo; conciliar por regras configuráveis e revisão manual.
- **CNAB:** especificar padrão (240/400), banco e versão do layout antes de implementar parser definitivo; iniciar com interface/adapter e fixtures documentadas, sem afirmar compatibilidade universal.
- **Extratos:** separar transação importada, transação interna e vínculo de conciliação; fornecer tratamento explícito para divergências.
- **Pix, boletos, cartões, webhooks e Open Finance:** começar por contratos internos, simuladores/adapters e testes. Integração real só quando existir provedor definido, credenciais legítimas e requisitos de segurança/compliance validados.
- **Nunca** enviar pagamentos reais, acessar contas bancárias ou consentir compartilhamento de dados automaticamente. Implementações iniciais devem operar em modo de demonstração/sandbox.

### 4.4.1 Estado funcional conhecido do repositório

Consulte `README.md`, `docs/progress.md`, `docs/api.md` e os testes antes de declarar algo concluído. O estado atual já inclui:

- Cadastro/login/logout, hash Argon2id, organizações isoladas, sessão assinada e CSRF.
- Páginas server-rendered Bootstrap para visão geral, contas, categorias, transações, títulos a pagar/receber, contabilidade, conciliação, relatórios e simuladores.
- Contas financeiras, categorias com nós sintéticos/analíticos, receitas/despesas, transferências, liquidação, estornos, extrato e saldos derivados.
- Títulos independentes (`FinancialTitle`), contatos (`Counterparty`) e baixas (`TitleSettlement`); cadastro não movimenta caixa. Baixa grava transação vinculada e diário contábil atomicamente. Títulos não são transações.
- Plano contábil, diário de partidas dobradas, livro razão, rascunho/efetivação/estorno e backfill idempotente.
- Importação OFX 1.x SGML e conciliação manual com deduplicação e auditoria. CNAB tem somente protocolo de adapter sem layout bancário implementado.
- Exportação de relatório CSV/PDF e simuladores locais sem integrações externas reais.
- API JSON `/api/v1` para sessão, contas, categorias, transações e títulos (listar, detalhar, cadastrar com parcelas, baixar e cancelar, com idempotência por `operation_key`). Contrato em `docs/api.md` e `docs/openapi.yaml`.
- Cadastros de produto (fase 8): produtos, grupos, unidades de medida e depósitos, com páginas `/products` e `/product-settings` e API `/api/v1/products`. 
- Estoque (fase 9): `StockMovement` imutável e `StockBalance` como cache da posição, custo médio ponderado, entrada/saída/transferência/estorno em `application/inventory.py`, páginas `/stock` e `/stock/movements`, API `/api/v1/stock` e lançamento contábil dos ajustes (`docs/accounting.md`). Documentos (compras, vendas) movimentam o estoque por `post_document_movements`/`revert_document_movements`, nunca gravando `StockMovement` ou `StockBalance` direto.
- Compras (fase 10): `PurchaseOrder`/`PurchaseReceipt` em `application/purchases.py`; o recebimento grava estoque, títulos a pagar (`create_document_titles`, origem `purchase`) e diário por competência numa única transação; páginas `/purchases` e API de consulta `/api/v1/purchase-orders`. Numeração por `next_document_number`. 
- Vendas (fase 11): `SalesOrder` (orçamento → confirmado → faturado) e `SalesInvoice` em `application/sales.py`; o faturamento grava saída de estoque, títulos a receber (origem `sale`) e os lançamentos de receita e CMV numa única transação; páginas `/sales` e API de consulta `/api/v1/sales-orders`. O faturamento é interno e sem valor fiscal. 
- CRM (fase 12): `Lead`, `Opportunity`, `PipelineStage`, histórico e atividades em `application/crm.py`; lead só vira `Counterparty` na conversão (deduplicada por CPF/CNPJ); páginas `/crm` e `/crm/leads` e API de consulta `/api/v1/crm/opportunities`. 
- Fiscal simulado (fase 13): `TaxProfile` e `FiscalDocument` em `application/fiscal.py`; documento interno gerado do faturamento, sem efeito em estoque, financeiro ou diário; páginas `/fiscal` e API de consulta `/api/v1/fiscal/documents`. **Não existe emissão fiscal real**, e toda tela e resposta da API precisam continuar dizendo que é simulação.
- Perfis `owner`, `finance`, `stock`, `purchasing`, `sales` e `viewer`, com permissão de escrita por área (financeiro, contatos, cadastros de produto, estoque, compras, vendas, CRM e fiscal simulado) (`ROLE_WRITE_AREAS` em `application/members.py` e `write_area` em `presentation/web.py`). Toda rota nova de um módulo do ERP precisa ser classificada em `write_area`; sem isso ela fica na área financeira.

Entidades Tortoise existentes: `Organization`, `User`, `Membership`, `FinancialAccount`, `Category`, `FinancialTransaction`, `Counterparty`, `FinancialTitle`, `TitleSettlement`, `LedgerAccount`, `JournalEntry`, `JournalEntryLine`, `ImportedStatementTransaction`, `ReconciliationMatch`, `ReconciliationEvent`, `ProductGroup`, `UnitOfMeasure`, `Product`, `Warehouse`, `StockMovement`, `StockBalance`, `DocumentSequence`, `PurchaseOrder`, `PurchaseOrderItem`, `PurchaseReceipt`, `PurchaseReceiptItem`, `SalesOrder`, `SalesOrderItem`, `SalesInvoice`, `SalesInvoiceItem`, `PipelineStage`, `Lead`, `Opportunity`, `OpportunityStageChange`, `CrmActivity`, `TaxProfile`, `FiscalDocument` e `FiscalDocumentItem`. Seus vínculos e responsabilidades estão no README e `src/fincore/infrastructure/models.py`. Não duplicar entidades antes de inspecionar o modelo e migrations.

Não recrie módulos funcionais já existentes nem mude seus contratos sem necessidade. Não assuma que todos os checkboxes do README indicam conclusão; confirme progress.md e testes. Recursos simulados, incompletos ou bloqueados precisam continuar explicitamente identificados.

### 4.5 Produtos, estoque e documentos comerciais (ERP)

Regras para as fases 8 a 13; detalhes e modelo de dados em `docs/erp-roadmap.md`.

- Reaproveitar `Counterparty` (clientes e fornecedores), `FinancialTitle`/`TitleSettlement` (contas a pagar e a receber) e o diário contábil. Compras e vendas **geram títulos**; não criar cadastro paralelo de contatos nem outra lógica de parcelas e baixas.
- SKU e código de barras são únicos por organização. Quantidades usam `Decimal` e `NUMERIC(15,4)` e respeitam as casas decimais da unidade de medida; nunca arredondar quantidade em silêncio.
- Cada organização tem exatamente um depósito padrão.
- Quantidade em estoque é derivada dos movimentos; nenhum campo de saldo editável é fonte da verdade. Um saldo guardado é só cache, gravado na mesma transação do movimento e coberto por teste que o compara aos movimentos.
- Movimento de estoque efetivado é imutável (um gatilho no banco recusa `UPDATE`/`DELETE`); correção por movimento de estorno com referência ao original. Só o último movimento de uma posição pode ser estornado; o passado é corrigido por ajuste.
- Parâmetros da URL são lidos por `_query(request)` (`presentation/web.py`), que decodifica o valor; não use `request.query_params` direto nas rotas.
- Estoque negativo é bloqueado, com bloqueio de linha contra saídas simultâneas. Custeio por custo médio ponderado móvel.
- Documento que gera estoque, título e diário grava tudo em uma única transação PostgreSQL ou não grava nada, e aceita `operation_key`.
- Funções chamadas por documentos (`post_document_movements`, `create_document_titles`, `next_document_number`) não abrem transação: o documento abre uma só e bloqueia a própria linha primeiro. Contas contábeis de sistema são garantidas antes de abrir a transação.
- Títulos de documento (`origin` diferente de `manual`) só são cancelados pelo estorno do documento, e a baixa deles quita a conta de contrapartida.
- Títulos gerados por compra ou venda lançam por competência (estoque/fornecedores, clientes/receita, CMV/estoque); títulos avulsos continuam lançando só na liquidação. Documentar em `docs/accounting.md` ao implementar.
- Fiscal é somente simulação: dados tributários informados pelo usuário, sem XML válido, assinatura ou transmissão à SEFAZ, e a interface diz isso.

### 4.6 Referências técnicas do FinCore

Utilizar os seguintes projetos como referência para compreender regras de negócio:

#### Django Ledger

https://github.com/arrobalytics/django-ledger

- Plano de contas
- Partidas dobradas
- Livro razão
- Demonstrações financeiras

#### ERPNext

https://github.com/frappe/erpnext

- Conciliação bancária
- Lançamentos contábeis
- Fluxos de pagamento

#### Firefly III

https://github.com/firefly-iii/firefly-iii

- Gestão de receitas e despesas
- Categorias
- Transferências
- Dashboard

#### Regras para consulta e reutilização

1. Manter Python + Robyn + Tortoise ORM.
2. Não migrar para Django, Frappe ou Laravel.
3. Utilizar os projetos apenas como referências de comportamento e regras de negócio.
4. Não copiar código de terceiros automaticamente.
5. Verificar as licenças antes de reutilizar qualquer código.
6. Implementar regras financeiras com testes.
7. Preservar o equilíbrio de partidas dobradas.
8. Não utilizar `float` para valores monetários.

## 5. API e interface

- API HTTP versionada em `/api/v1`, com contratos documentados em `docs/api.md` (método, rota, payload, resposta, autenticação e erros).
- Usar códigos HTTP apropriados, validação de entrada no limite da aplicação, paginação e filtros para listas.
- Erros com formato consistente e sem revelar stack traces ou segredos ao cliente.
- Interface responsiva em pt-BR, com acessibilidade básica, estados de carregamento e mensagens de erro claras.
- Não expor funcionalidades como prontas na interface quando forem apenas simuladas; sinalizar claramente o ambiente de demonstração.
- A interface atual é renderizada no servidor com Robyn/Jinja/Bootstrap. Formulários seguem sessão e CSRF; mensagens de confirmação usam modal da interface, não `alert()`/`confirm()` nativos do JavaScript.
- Páginas atuais incluem: `/login`, `/register`, `/dashboard`, `/accounts`, `/categories`, `/transactions`, `/payables`, `/receivables`, `/accounting`, `/reconciliation`, `/reports`, `/integrations`, `/members`, `/products`, `/product-settings`, `/stock`, `/stock/movements`, `/purchases`, `/sales`, `/crm`, `/crm/leads` e `/fiscal`. Verifique `docs/api.md` para métodos e limites completos.
- Títulos têm páginas HTML (`/payables`, `/receivables`, `/titles/...`) e API JSON (`/api/v1/titles`). Mudanças na API de títulos atualizam `docs/openapi.yaml` e devem continuar passando no `openapi-spec-validator`.

## 6. Segurança mínima obrigatória

- Armazenar senhas com algoritmo de hash apropriado, nunca em texto puro.
- Autenticação segura, autorização em **cada** ação e proteção contra acesso a dados de outros usuários/organizações.
- Proteção contra CSRF quando usar autenticação por cookie; cookies seguros, `HttpOnly` e `SameSite` conforme o fluxo.
- Validação server-side, queries parametrizadas pelo ORM e cuidados com XSS em templates.
- Segredos somente via variáveis de ambiente; `.env` ignorado pelo Git, `.env.example` sem valores reais.
- Logs estruturados sem dados bancários sensíveis, tokens, senhas ou chaves.
- Para webhooks: assinatura/autenticidade, proteção contra replay, idempotência e tratamento de falhas.
- Não anunciar conformidade com LGPD, PCI DSS, Open Finance ou normas bancárias sem avaliação específica.

## 7. Banco, Docker e migrations

- Manter aplicação e banco em containers separados; o banco fica só na rede interna `backend`, e o container `app` usa o usuário restrito `APP_DB_USER` (sem DDL). Migrations rodam pelo serviço `migrate` com o usuário dono.
- Usar `compose.yaml` com PostgreSQL, volume persistente e healthcheck que utilize **as mesmas variáveis** `POSTGRES_USER` e `POSTGRES_DB` configuradas no serviço.
- Não fixar credenciais reais no Compose nem no código; usar `.env` local. Mapear portas locais apenas quando necessário.
- Criar índices, constraints, chaves estrangeiras e restrições de unicidade para integridade no banco.
- Migrations versionadas são obrigatórias; nunca usar `generate_schemas()` como mecanismo de produção.
- Manter ambiente de teste isolado do desenvolvimento; testes não devem apagar dados reais.
- Incluir seed **apenas para desenvolvimento**, com dados fictícios claramente sinalizados.
- Para setup local reproduzível siga a seção de instalação do README. `docker compose up -d --build` sobe aplicação, migrations e banco em containers separados (banco só na rede interna); `docker compose -f compose.yaml -f compose.dev.yaml up -d db` publica o PostgreSQL de desenvolvimento em `127.0.0.1:5433` para rodar a aplicação fora do Docker; `docker compose --profile test up -d db-test` inicia banco de teste separado em `127.0.0.1:5434`.
- `.env.example` contém somente credenciais locais descartáveis. Não copie valores locais para produção. A inicialização padrão do produto não possui dados seed e o dashboard não pode usar fixtures como dados reais.
- Alterações de schema são aditivas por padrão. Faça backup antes de migration de risco, documente rollback e nunca remova volumes ou registros para “limpar” o ambiente. Os testes não podem chamar comandos de reset, truncate ou exclusão global.

## 8. Testes e critérios de pronto

Para cada funcionalidade:

1. Implementar a regra de negócio e validações.
2. Adicionar testes unitários de cenários válidos e inválidos.
3. Adicionar testes de integração de persistência, transações, autorização e endpoints relevantes.
4. Cobrir casos críticos: valor monetário, duplicidade, concorrência, estorno e balanço contábil.
5. Rodar formatação/lint, testes e migrations em banco limpo.
6. Documentar endpoint, configuração e decisões relevantes.

Para o projeto atual, use o banco isolado documentado no README. Se não houver Docker/PostgreSQL, execute os testes que não dependem de banco e registre precisamente quais integrações ficaram sem validação. Não aponte suíte de integração ao banco de desenvolvimento.

**Não marcar uma tarefa como concluída** se houver testes falhando ou se a execução não tiver sido verificada. Se o ambiente impedir a validação, registrar explicitamente o bloqueio e os comandos que faltam executar. Não inventar resultados de testes.

Condições de aceite mínimas por módulo:
- Autenticação: acesso não autenticado negado e isolamento de dados demonstrado.
- Transações: criar/listar/consultar sem duplicidade; erros deixam o banco consistente.
- Contabilidade: lançamento desbalanceado é rejeitado; efetivado não pode ser alterado; estorno preserva histórico.
- OFX: importação repetida não duplica extratos; arquivo inválido produz erro controlado.
- Conciliação: match e desmatch têm rastreabilidade e não alteram indevidamente lançamentos contábeis.
- Dashboard: indicadores batem com consultas de referência e distinguem realizado de previsto.

## 9. Ordem de implementação

Concluir uma fase com testes e documentação antes de iniciar a próxima:

1. **Fundação:** projeto uv, Robyn, PostgreSQL, Tortoise, migrations, Docker, configurações, `/health`, logging, CI e testes básicos.
2. **Identidade:** usuários, login/logout, autorização e isolamento de organizações.
3. **Núcleo financeiro:** contas, categorias, receitas, despesas, transferências, liquidação e extratos internos.
4. **Contabilidade:** plano de contas, diário, partidas dobradas, ledger, estornos, conciliação com o núcleo financeiro.
5. **Conciliação:** importação OFX, extratos externos, matching, divergências e revisão manual; CNAB por layout especificado.
6. **Apresentação:** dashboards, filtros, relatórios e exportação CSV/PDF com testes.
7. **Integrações simuladas:** interfaces para Pix, boletos, cartões, webhooks e Open Finance com mocks, documentação e testes; produção fica fora de escopo sem requisitos próprios.
8. **Cadastros de produto (concluída):** produtos, grupos, unidades de medida e depósitos.
9. **Estoque (concluída):** movimentos imutáveis, saldo derivado, custo médio, ajustes e transferências entre depósitos.
10. **Compras (concluída):** pedido de compra, recebimento, entrada em estoque e contas a pagar.
11. **Vendas (concluída):** orçamento, pedido, faturamento, baixa de estoque e contas a receber.
12. **CRM (concluída):** leads, funil de oportunidades e atividades ligados a vendas.
13. **Fiscal simulado (concluída):** documento fiscal interno com dados tributários informados, sem SEFAZ.

Registrar em `docs/progress.md` o que foi implementado, validado, pendências e próximo passo. Atualizar os checkboxes do README apenas após implementação validada.

## 10. Fluxo de trabalho para o Codex

1. Inspecione o repositório, dependências e documentação antes de editar.
2. Se o projeto estiver vazio, crie os arquivos mínimos da fase 1. Não sobrescreva código existente sem inspeção.
3. Escreva um plano curto e trabalhe em pequenas entregas. **Prossiga de fase em fase** sem pedir aprovação para decisões técnicas rotineiras e reversíveis.
4. Consulte documentação oficial quando uma versão de Robyn, Tortoise, uv ou outra biblioteca causar dúvida. Não invente APIs/comandos.
5. Execute os comandos de validação que estiverem disponíveis. Se não houver Docker ou acesso à rede, mantenha código e testes preparados e registre a limitação.
6. Ao término de cada fase, apresente um resumo: arquivos alterados, funcionalidades, testes executados, resultados e riscos restantes.
7. Caso uma tarefa dependa de credenciais, contrato bancário, informação legal ou decisão de alto risco ausente, implemente somente um adapter simulado; registre o bloqueio e continue nas outras partes seguras.
8. Nunca exclua dados, altere recursos externos, realize pagamentos ou execute comandos destrutivos sem autorização explícita.
9. Antes de modificar, inspecione código, migrations, documentação, status do Git e ambiente. Preserve arquivos e dados preexistentes.
10. Faça as alterações necessárias até deixá-las implementadas, documentadas e verificáveis; não pare em plano ou tela de demonstração quando a tarefa pedir funcionalidade completa.
11. Mantenha `README.md` como guia executável de setup e `docs/progress.md` como registro verdadeiro de validação. Só marque funcionalidade como pronta após executar seus critérios.
12. **Toda implementação, correção ou melhoria atualiza o `README.md` no mesmo commit:** acrescente uma linha na tabela "Atualizações recentes" e ajuste as seções afetadas (funcionalidades, número da última migration, comandos, estrutura). Não faça commit de mudança de comportamento sem a atualização correspondente do README.

## 11. Como começar

Na primeira execução, o Codex deve:
- Ler `README.md` e este `AGENTS.md`.
- Criar/atualizar `docs/requirements.md` com histórias de usuário, regras e critérios de aceite da fase 1.
- Inspecionar os arquivos reais e verificar compatibilidade das versões escolhidas.
- Implementar **a fase 1 integralmente**, com uma API Robyn funcional e PostgreSQL via Docker, sem criar integrações bancárias reais.
- Após validar a fase 1, seguir as fases seguintes na ordem indicada, mantendo progresso e documentação. Se não puder concluir uma fase, registrar por que e retomar pelo próximo passo seguro.

Para construir o FinCore do zero usando somente estes documentos e o README:

1. Trate o README como requisitos do produto, instalação e contratos visíveis; leia os documentos ligados em `docs/` e use este arquivo como restrições de arquitetura/segurança.
2. Inspecione o ambiente, ferramentas e arquivos antes de assumir que estão ausentes. Instale dependências com `uv sync`; use `cp .env.example .env` para ambiente local e suba o PostgreSQL com `docker compose -f compose.yaml -f compose.dev.yaml up -d db` sem apagar volumes existentes.
3. Implemente as fases em ordem e em incrementos utilizáveis. O Compose precisa publicar o PostgreSQL local, persistir dados em volume, validar saúde e oferecer serviço isolado para testes. Faça `/health`, migrations e fluxos de execução funcionarem antes dos módulos.
4. Entregue páginas Bootstrap renderizadas no servidor junto dos casos de uso, persistência e validação; uma API ou protótipo visual sozinho não satisfaz o produto.
5. Rode migrations e testes no banco de teste. Não interrompa o trabalho para pedir decisões rotineiras/reversíveis; escolha defaults documentados. Pergunte apenas quando um dado de alto risco, credencial legítima ou contrato externo for indispensável. Mantenha simuladores quando esses itens faltarem.
6. Finalize com README reproduzível, `.env.example` utilizável só localmente, contratos, decisões, progresso verdadeiro, testes e instruções de operação Docker. Não marque o projeto pronto sem verificar inicialização, persistência e fluxos principais.

**Definição de sucesso:** o projeto roda localmente com instruções reproduzíveis, apresenta funcionalidades reais testadas, mantém integridade contábil, separa integrações simuladas de reais e não expõe segredos.
