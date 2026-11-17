# FinCore ERP — roteiro de transformação

> **Situação: aprovado em 2026-11-17.** Fases 8 a 12 concluídas; fase 13 pendente. Cada fase segue o fluxo do `AGENTS.md` (regra, testes, migration, documentação) e o que foi validado fica em `docs/progress.md`.

## 1. Objetivo e escopo

Transformar o FinCore de sistema financeiro e contábil em um **ERP para comércio (venda de produtos)**, mantendo a stack (Python, Robyn, Tortoise ORM, PostgreSQL, páginas Jinja + Bootstrap) e o monólito modular em camadas.

Módulos novos, na ordem de entrega:

| Fase | Módulo | Resultado utilizável |
|---|---|---|
| 8 | Cadastros de produto ✅ | Produtos, unidades, grupos e depósitos |
| 9 | Estoque ✅ | Entradas, saídas, ajustes, transferências, saldo e custo médio |
| 10 | Compras ✅ | Pedido de compra → recebimento → estoque + contas a pagar |
| 11 | Vendas ✅ | Orçamento → pedido → faturamento → baixa de estoque + contas a receber |
| 12 | CRM ✅ | Leads, funil de oportunidades e atividades ligados a vendas |
| 13 | Fiscal (simulado) | Documento fiscal interno com dados tributários, sem SEFAZ |

As fases 1 a 7 do `AGENTS.md` (fundação até integrações simuladas) continuam como estão.

Fora de escopo neste roteiro: produção/manufatura, ordens de serviço, folha de pagamento, emissão fiscal real, PDV e e-commerce.

## 2. O que já existe e será reaproveitado

- **`Organization`, `Membership` e perfis:** todo cadastro novo nasce com `organization` e passa pelo middleware de escrita e pelo teste de isolamento entre organizações.
- **`Counterparty`:** já é o cadastro de clientes e fornecedores (CPF/CNPJ, endereço, `is_customer`/`is_supplier`). Compras, Vendas e CRM usam essa entidade; não será criada outra.
- **`FinancialTitle` e `TitleSettlement`:** pedidos faturados e recebimentos de compra **geram títulos**; a baixa continua no módulo financeiro, sem duplicar lógica de parcelas.
- **`JournalEntry` e plano de contas:** estoque, compras e vendas lançam no diário existente.
- **Padrões:** `Decimal`, idempotência por `operation_key`, estorno por registro compensatório, paginação de 50 itens, `application/queries.py` para leituras, regras puras em `domain/`.

## 3. Decisões aprovadas

Estas escolhas mudam o modelo de dados e são caras de reverter depois. Foram aprovadas como propostas.

### 3.1 Contabilidade por competência nos documentos de compra e venda

Hoje o diário só é lançado na **liquidação** (regime de caixa): despesa debita despesa e credita o banco. Com estoque isso não fecha, porque a mercadoria comprada a prazo precisa aparecer no ativo antes do pagamento.

**Proposta:** títulos gerados por compra ou venda lançam por competência; títulos avulsos continuam como hoje.

| Evento | Débito | Crédito |
|---|---|---|
| Recebimento de compra | Estoque (ativo) | Fornecedores (passivo) |
| Baixa do título da compra | Fornecedores | Banco/caixa |
| Faturamento de venda | Clientes (ativo) | Receita de vendas |
| Custo da venda (mesmo momento) | CMV (despesa) | Estoque |
| Baixa do título da venda | Banco/caixa | Clientes |
| Ajuste de estoque (perda/sobra) | Perdas / Estoque | Estoque / Ganhos |

Impacto: `FinancialTitle` ganha origem (`manual`, `purchase`, `sale`) e a conta contábil de contrapartida; `settle_title` escolhe o lançamento pela origem. Cada organização ganha contas padrão (Estoque, Fornecedores, Clientes, Receita de vendas, CMV), configuráveis.

### 3.2 Custeio por custo médio ponderado móvel

Cada entrada recalcula o custo médio do produto no depósito; cada saída usa o custo médio vigente. É o método mais simples e aceito no Brasil. PEPS (FIFO) exigiria controle de lotes e fica fora.

### 3.3 Estoque negativo bloqueado

Saída maior que o saldo é recusada, com bloqueio de linha (`SELECT ... FOR UPDATE`) para vendas simultâneas. Pode virar configuração por organização depois.

### 3.4 Perfis de acesso

Manter Proprietário e Somente leitura, e acrescentar `Vendas`, `Compras` e `Estoque` ao lado de `Financeiro`, cada um escrevendo só na própria área. O perfil `Estoque` foi criado na fase 8; `Compras` e `Vendas` nascem nas fases 10 e 11, junto com as áreas que operam.

### 3.5 Fiscal apenas como simulação

O módulo guarda NCM, CFOP, CST/CSOSN e alíquotas **informados pelo usuário** e monta um documento fiscal interno numerado. Não calcula imposto como verdade, não gera XML válido, não assina e não transmite à SEFAZ. A tela deixa explícito que é demonstração, como os simuladores de Pix e boleto.

## 4. Modelo de dados por fase

Nomes provisórios; todas as tabelas têm `organization`, `created_by`, `created_at` e migrations aditivas.

### Fase 8 — Cadastros de produto (concluída)

- `ProductGroup`: grupo hierárquico de produtos.
- `UnitOfMeasure`: sigla e casas decimais (UN, KG, CX).
- `Product`: código/SKU único por organização, descrição, grupo, unidade, código de barras, preço de venda, estoque mínimo, controla estoque (sim/não), ativo. Campos fiscais (NCM, origem) como referência.
- `Warehouse`: depósito; a organização nasce com um depósito padrão.

Aceite: cadastro, edição, inativação e busca; SKU duplicado recusado; isolamento entre organizações; páginas `/products` e `/product-settings` (grupos, unidades e depósitos); API `/api/v1/products`.

### Fase 9 — Estoque (concluída)

- `StockMovement`: imutável. Produto, depósito, sentido (`in`/`out`), motivo (saldo inicial, ajuste, transferência, estorno; `purchase` e `sale` reservados), quantidade `NUMERIC(15,4)`, custo unitário e total, saldo após o movimento, origem (documento), `operation_key`, referência ao movimento estornado e ao lançamento contábil.
- `StockBalance`: quantidade e valor total por produto/depósito (custo médio = valor ÷ quantidade), mantido na mesma transação do movimento como **cache**; a fonte da verdade são os movimentos, com teste que compara os dois.
- `domain/inventory.py`: custo médio, validação de saldo, arredondamento.

Aceite: saldo sempre igual à soma dos movimentos; saída sem saldo recusada inclusive em concorrência; reenvio não duplica; estorno preserva histórico; transferência entre depósitos não altera o custo total; ajuste gera lançamento contábil balanceado; páginas `/stock` (posição, abaixo do mínimo, formulário de movimentação) e `/stock/movements` (histórico e estorno); API `/api/v1/stock`.

Regra acrescentada na implementação: só o último movimento de uma posição pode ser estornado (o passado é corrigido por ajuste).

### Fase 10 — Compras (concluída)

- `PurchaseOrder` e `PurchaseOrderItem`: fornecedor, itens, quantidades, custo, condição de pagamento. Estados: rascunho → aprovado → recebido parcial → recebido → cancelado.
- `PurchaseReceipt` e `PurchaseReceiptItem`: recebimento total ou parcial; gera, de forma atômica, movimentos de estoque, títulos a pagar (com parcelas) e lançamento contábil.

Aceite: recebimento parcial atualiza o saldo do pedido; não se recebe mais que o pedido; cancelamento só sem recebimento; estorno de recebimento reverte estoque e cancela títulos sem baixa; soma das parcelas igual ao total.

### Fase 11 — Vendas (concluída)

Implementado de forma mais simples que o previsto abaixo: orçamento e pedido são o mesmo registro (`SalesOrder`) em estados diferentes, o faturamento é do pedido inteiro e a devolução é total.

- `SalesQuote` e itens: orçamento com validade; pode virar pedido.
- `SalesOrder` e `SalesOrderItem`: cliente, itens, preço, desconto, condição. Estados: rascunho → confirmado → faturado parcial → faturado → cancelado.
- `SalesInvoice` (faturamento interno): baixa estoque, gera títulos a receber e lança receita e CMV na mesma transação.
- Reserva de estoque na confirmação fica como melhoria posterior; no início o estoque é validado no faturamento.

Aceite: faturamento sem estoque é recusado e nada é gravado; desconto e total em `Decimal` exato; dois faturamentos simultâneos do último item, só um passa; devolução gera entrada de estoque e estorno contábil.

### Fase 12 — CRM (concluída)

Implementado com funil padrão fixo (sem tela de configuração de etapas) e quadro sem arrastar e soltar.

- `Lead`: contato ainda não cliente; conversão cria ou vincula um `Counterparty`.
- `PipelineStage` e `Opportunity`: funil configurável, valor estimado, responsável, motivo de perda.
- `Activity`: tarefa, ligação, reunião ou nota, ligada a lead, oportunidade ou cliente.
- Oportunidade ganha gera orçamento (fase 11).

Aceite: funil em quadro e em lista; histórico de mudanças de etapa; conversão de lead não duplica contato com mesmo CPF/CNPJ; indicadores de conversão conferidos por consulta de referência.

### Fase 13 — Fiscal (simulado)

- `TaxProfile`: regras informadas (CFOP, CST/CSOSN, alíquotas) por operação.
- `FiscalDocument` e itens: gerado a partir do faturamento ou do recebimento, com numeração e série internas e totais por imposto. Estados: rascunho → emitido (simulado) → cancelado.

Aceite: totais do documento batem com o faturamento; numeração sem buracos nem duplicidade em concorrência; interface e API marcam o documento como simulação.

## 5. Mudanças transversais

- **Navegação:** menu lateral agrupado (Vendas, Compras, Estoque, Financeiro, Contabilidade, Cadastros, Configurações) e visão geral com indicadores comerciais.
- **Código:** `infrastructure/models.py` vira pacote com um arquivo por área, sem alterar tabelas; cada módulo ganha arquivo próprio em `domain/`, `application/` e `presentation/routes/`.
- **Seed de demonstração:** `fincore-seed-demo` passa a incluir produtos, compras e vendas fictícios.
- **Documentação:** `AGENTS.md` (missão, fases 8–13, invariantes de estoque), README, `docs/accounting.md` (competência), `docs/api.md` e `docs/openapi.yaml` atualizados a cada fase.

## 6. Invariantes novas

1. Quantidade em estoque é derivada dos movimentos; nenhum campo de saldo editável é fonte da verdade.
2. Movimento de estoque efetivado é imutável; correção por movimento de estorno.
3. Documento que gera estoque, título e diário grava tudo em uma única transação PostgreSQL ou não grava nada.
4. Quantidades em `NUMERIC(15,4)` e valores em `NUMERIC(15,2)`, sempre `Decimal`.
5. Todo documento com efeito externo (recebimento, faturamento, ajuste) aceita `operation_key`.
6. Fiscal nunca se apresenta como emissão real.

## 7. Próximo passo

**Fase 10 (compras):** pedido de compra, recebimento total ou parcial com entrada em estoque (motivo `purchase`) e títulos a pagar lançados por competência (D Estoque / C Fornecedores). Concluída.

**Fase 13 (fiscal simulado):** documento fiscal interno gerado do faturamento, com dados tributários informados e sem SEFAZ.
