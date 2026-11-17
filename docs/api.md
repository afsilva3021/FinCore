# Aplicação web e endpoints

As páginas HTML são a interface principal do FinCore. Os formulários fazem validação server-side e dados são escapados pelos templates Jinja.

## Páginas

- `GET /login`, `POST /login` — autenticação por e-mail e senha. `401` para credenciais inválidas; `429` quando o e-mail (5 falhas) ou o IP (20 falhas) atingiu o limite em 15 minutos, por 15 minutos.
- `GET /register`, `POST /register` — criação da organização e usuário proprietário. Exige `password_confirm` igual a `password` e senha de 12 a 128 caracteres com pelo menos 3 tipos (minúscula, maiúscula, número, símbolo), sem conter o nome ou o início do e-mail. A regra fica em `fincore.application.identity.password_problems`; o JavaScript da página só a espelha para retorno imediato.
- `GET /dashboard` — página inicial autenticada.
- `POST /logout` — encerra a sessão e revoga todos os tokens de sessão do usuário (incrementa `session_version`).
- `GET /accounts`, `POST /accounts` — contas financeiras e saldos derivados.
- `GET /categories`, `POST /categories` — categorias de receita/despesa, grupos sintéticos e subcategorias analíticas.
- `GET /transactions`, `POST /transactions` — receitas, despesas e transferências.
- `POST /transactions/:transaction_id/settle` — liquida movimentação em aberto.
- `POST /transactions/:transaction_id/reverse` — cria movimento compensatório e preserva o original.
- `GET /accounting` — plano de contas e diário.
- `POST /accounting/accounts`, `POST /accounting/journals` — contas contábeis e rascunhos.
- `POST /accounting/journals/:entry_id/update`, `/post`, `/reverse` — altera rascunhos, efetiva ou cria estorno.
- `GET /static/*` — CSS e JavaScript da interface.
- `GET /contacts`, `/customers`, `/suppliers` — contatos, clientes ou fornecedores; filtros `role` e `status` são aplicados na tela.
- `GET /contacts/:contact_id/edit` — formulário lateral de edição do contato autenticado.
- `POST /contacts`, `POST /contacts/:contact_id/update` — cria ou atualiza cliente/fornecedor, com CSRF.
- `POST /contacts/:contact_id/active` — inativa ou reativa contato; dados e vínculos históricos são preservados.
- `GET /contacts/postal-code?cep=01001000` — consulta ViaCEP, exige sessão e retorna endereço em JSON; erros upstream são controlados.
- `GET /products` — produtos com busca (`q`), filtros `status` e `group` e paginação.
- `GET /products/:product_id/edit` — formulário lateral de edição.
- `POST /products`, `POST /products/:product_id/update` — cria ou altera produto, com CSRF; erro de validação responde `400` com o formulário preenchido.
- `POST /products/:product_id/active` — inativa ou reativa o produto.
- `GET /product-settings` — grupos de produto, unidades de medida e depósitos.
- `POST /product-settings/groups`, `/units`, `/warehouses` — cria; `POST .../:item_id/update` altera; `POST .../:item_id/active` inativa ou reativa.
- `POST /product-settings/warehouses/:item_id/default` — define o depósito padrão (a organização sempre tem exatamente um).
- `GET /stock` — posição de estoque por produto e depósito, valor total ao custo médio, produtos abaixo do mínimo; filtros `q`, `warehouse` e `in_stock=1`.
- `POST /stock/movements` — registra movimentação: `kind` = `opening` (saldo inicial), `entry` (ajuste de entrada), `exit` (ajuste de saída) ou `transfer`; campos `operation_key`, `product_id`, `warehouse_id`, `destination_warehouse_id` (transferência), `quantity`, `unit_cost` (entradas), `occurred_on` e `notes`. Recusa responde `400` com o motivo e o formulário preenchido.
- `GET /stock/movements` — histórico de movimentos (filtros `product` e `warehouse`).
- `POST /stock/movements/:movement_id/reverse` — estorna o último movimento da posição (a transferência inteira, se for o caso).
- `GET /purchases` — pedidos de compra (filtros `q` e `status`); `POST /purchases` cria um pedido em rascunho com itens `product_N`, `quantity_N` e `unit_cost_N`.
- `GET /purchases/:order_id` — itens, recebimentos e contas a pagar do pedido.
- `POST /purchases/:order_id/approve`, `/cancel` — aprova o rascunho ou cancela um pedido sem mercadoria recebida.
- `POST /purchases/:order_id/receipts` — recebimento total ou parcial (`qty_<item>`, `received_on`, `document_number`, `operation_key`): entra no estoque, gera contas a pagar e lança no diário.
- `POST /purchases/receipts/:receipt_id/reverse` — estorna o recebimento.
- `GET /sales` — orçamentos e pedidos de venda (filtros `q` e `status`); `POST /sales` cria um orçamento com itens `product_N`, `quantity_N`, `unit_price_N` (em branco = preço do cadastro) e `discount_N`.
- `GET /sales/:order_id` — itens, faturamentos, margem e contas a receber.
- `POST /sales/:order_id/confirm`, `/cancel` — confirma o orçamento como pedido ou cancela antes do faturamento.
- `POST /sales/:order_id/invoices` — fatura o pedido inteiro (`issued_on`, `operation_key`): baixa estoque, gera contas a receber e lança receita e custo.
- `POST /sales/invoices/:invoice_id/reverse` — devolução total: estorna o faturamento.
- `GET /crm` — quadro do funil, indicadores e próximas atividades; `POST /crm/opportunities` cria oportunidade para um lead (`lead_id`) ou cliente (`counterparty_id`).
- `GET /crm/opportunities/:opportunity_id` — detalhe, histórico de etapas e atividades; `POST .../stage` muda a etapa (`stage_id`, `lost_reason`); `POST .../activities` registra atividade.
- `POST /crm/activities/:activity_id/done` — conclui a atividade.
- `GET /crm/leads`, `POST /crm/leads` — leads; `POST /crm/leads/:lead_id/convert` (`person_type`, `tax_id`) converte em cliente; `POST /crm/leads/:lead_id/discard` descarta.

Sessões usam cookie assinado, HttpOnly e SameSite=Lax; formulários de alteração de estado validam token CSRF.

## API v1

## `GET /health`

Endpoint operacional sem autenticação para verificar a aplicação e a conexão com PostgreSQL.

- **200** — `{"status":"ok","database":"connected"}`
- **503** — `{"status":"unavailable","database":"disconnected"}`
- Não requer payload. A resposta não contém detalhes de exceções ou credenciais.

## Endpoints JSON v1

As rotas JSON usam o mesmo cookie de sessão assinado das páginas. Listas sempre limitam dados à organização da sessão. `limit` aceita 1–100 (padrão 50) e `offset` começa em zero.

| Método | Rota | Parâmetros | Resposta |
|---|---|---|---|
| GET | `/api/v1/health` | nenhum | `200 {"status":"ok","version":"v1"}` |
| GET | `/api/v1/session` | sessão autenticada | `200 {"csrf_token":"..."}` e cookie CSRF assinado |
| GET | `/api/v1/accounts` | `limit`, `offset` | `{items:[{id,name,type,bank_name,bank_code,branch_number,account_number,currency,balance}],limit,offset,total}` |
| POST | `/api/v1/accounts` | JSON `{name,type,opening_balance?,bank_name?,bank_code?,branch_number?,account_number?}` | `201` com a conta criada |
| GET | `/api/v1/categories` | `limit`, `offset` | `{items:[{id,name,transaction_type,category_kind,parent_id,ledger_account_id}],limit,offset,total}` |
| POST | `/api/v1/categories` | JSON `{name,transaction_type,category_kind?,parent_id?}` | `201` com a categoria criada; por padrão, analítica e sem superior |
| GET | `/api/v1/transactions` | `limit`, `offset`, filtro opcional `status=open|settled`, `type=income|expense|transfer_in|transfer_out` | `{items:[...],limit,offset,total}` |
| GET | `/api/v1/transactions/{id}` | UUID da movimentação | Movimento JSON ou 404 |
| POST | `/api/v1/transactions` | JSON documentado abaixo | `201` criada, `200` repetição idempotente |
| POST | `/api/v1/transactions/{id}/settle` | sem payload | `200` com movimento liquidado, `settled_at` e `settled_by_id` |
| POST | `/api/v1/transactions/{id}/reverse` | JSON `{operation_key,competence_date?}` | `201` criado, `200` repetido com a mesma chave |
| GET | `/api/v1/categories/suggestion` | `description`, `type=income\|expense` | `{suggestion:{category_id,name,confidence,examples}}` ou `{suggestion:null}` quando o histórico não sustenta uma sugestão (mínimo 3 exemplos e 60% de confiança) |
| GET | `/api/v1/titles` | `limit`, `offset`, `type=payable\|receivable`, `status=open\|partial\|settled\|cancelled` | `{items:[título com open_balance e overdue],limit,offset,total}` |
| GET | `/api/v1/titles/{id}` | UUID do título | Título com `settlements[]` ou 404 |
| POST | `/api/v1/titles` | JSON `{operation_key,type,description,counterparty_name,account_id,amount,issue_date,due_date,payment_method,installments?,category_id?,document_number?,notes?}` | `201 {items:[...]}`; reenvio com a mesma chave devolve os mesmos títulos; chave de outra organização = `409` |
| POST | `/api/v1/titles/{id}/settlements` | JSON `{operation_key,amount_applied,paid_on,account_id,payment_method,discount?,interest?,fine?,fees?}` | `201` com a baixa; reenvio idempotente; regra violada = `400` com o motivo |
| POST | `/api/v1/titles/{id}/cancel` | sem payload | `200` com o título cancelado; com baixa ou já liquidado = `409` |
| GET | `/api/v1/products` | `limit`, `offset`, `q` (código, descrição ou código de barras), `status=all\|active\|inactive` | `{items:[{id,sku,name,description,barcode,ncm,unit:{id,code},group,sale_price,currency,min_stock,tracks_stock,is_active}],limit,offset,total}` |
| GET | `/api/v1/products/{id}` | UUID do produto | Produto JSON ou 404 |
| POST | `/api/v1/products` | JSON `{sku,name,unit_id,group_id?,description?,barcode?,ncm?,sale_price?,min_stock?,tracks_stock?}`; `sale_price` e `min_stock` são strings decimais | `201` com o produto; dado inválido = `400 invalid_product`; SKU ou código de barras repetido = `409 conflict` |
| GET | `/api/v1/stock/balances` | `limit`, `offset`, `q`, `product_id`, `warehouse_id` | `{items:[{product_id,sku,product_name,unit,warehouse_id,warehouse_code,quantity,average_cost,total_value}],limit,offset,total}` |
| GET | `/api/v1/stock/movements` | `limit`, `offset`, `product_id`, `warehouse_id` | `{items:[movimento],limit,offset,total}`, do mais recente para o mais antigo |
| POST | `/api/v1/stock/movements` | JSON `{type,operation_key,product_id,warehouse_id,quantity,occurred_on,unit_cost?,destination_warehouse_id?,notes?}`; `type` = `opening\|entry\|exit\|transfer`; `quantity` e `unit_cost` são strings decimais | `201 {items:[...]}` (dois movimentos numa transferência); reenvio com a mesma chave = `200` com os mesmos movimentos; regra violada ou saldo insuficiente = `400 invalid_stock_movement`; chave de outra organização = `409 conflict` |
| POST | `/api/v1/stock/movements/{id}/reverse` | sem payload | `201 {items:[estornos]}`; movimento que não é o último, já estornado ou que é um estorno = `409 invalid_state` |
| GET | `/api/v1/purchase-orders` | `limit`, `offset`, `status` | `{items:[pedido com itens e total],limit,offset,total}` |
| GET | `/api/v1/purchase-orders/{id}` | UUID do pedido | Pedido com `items[]` (`quantity`, `unit_cost`, `received_quantity`) ou 404 |
| GET | `/api/v1/sales-orders` | `limit`, `offset`, `status` | `{items:[pedido com itens e total],limit,offset,total}` |
| GET | `/api/v1/sales-orders/{id}` | UUID do pedido | Pedido com `items[]` (`quantity`, `unit_price`, `discount`, `total`) ou 404 |
| GET | `/api/v1/crm/opportunities` | `limit`, `offset` | `{items:[{id,title,stage:{id,name,kind},party,lead_id,counterparty_id,expected_value,expected_close,lost_reason,sales_order_id}],limit,offset,total}` |

A especificação OpenAPI 3.1 dos endpoints de títulos, produtos, estoque, compras, vendas, CRM e de sessão está em [`docs/openapi.yaml`](openapi.yaml) (validada com `openapi-spec-validator`).

### Sessão, CSRF e escrita

Os endpoints aceitam a sessão por cookie assinada, a mesma usada pelas páginas. Após login, consulte `GET /api/v1/session`; a resposta inclui `csrf_token` e define o cookie CSRF assinado. Envie esse valor no header `X-CSRF-Token` em todo POST. Requisições de escrita sem token válido retornam 403. A sessão continua em cookie HttpOnly e não é devolvida no JSON.

Exemplo de receita/despesa:

```json
{
  "operation_key": "e8e3fb83-a8ab-4ee9-a6b6-fab56d19b93a",
  "type": "income",
  "account_id": "0876dc80-65b2-4561-8f71-1f6f4d1161bb",
  "category_id": null,
  "description": "Serviço prestado",
  "amount": "250.00",
  "competence_date": "2026-11-04",
  "due_date": null,
  "settled": true
}
```

Para transferência use `type: "transfer"`, `account_id` para origem e `target_account_id` para destino; categoria e `settled` não se aplicam. O servidor cria os dois movimentos e o lançamento contábil em uma operação atômica. Chaves repetidas com o mesmo payload retornam o registro existente; reutilizar a chave com payload diferente retorna 409 `idempotency_conflict`.

Sem sessão, respostas de negócio retornam 401 com `{"error":{"code":"unauthenticated","message":"Sessão necessária."}}`. Paginação inválida retorna 400 com `invalid_pagination`; valores monetários são strings decimais e datas usam ISO 8601. As páginas HTML continuam a experiência principal.
# Rotas web da aplicação

A interface principal é renderizada no servidor e requer sessão de usuário; formulários POST validam token CSRF.

| Método | Rota | Ação | Resultado |
|---|---|---|---|
| GET | `/reconciliation` | Lista itens de extrato e candidatos da mesma conta/data | Página HTML |
| POST | `/reconciliation/import` | Importa OFX 1.x SGML para uma conta | HTML; FITIDs repetidos são ignorados |
| POST | `/reconciliation/{statement_id}/match` | Concilia item com movimento liquidado de mesma conta/data/valor | Redirect ou erro HTML |
| POST | `/reconciliation/{statement_id}/unmatch` | Desfaz vínculo e registra evento de auditoria | Redirect ou erro HTML |
| GET | `/reports?start=AAAA-MM-DD&end=AAAA-MM-DD` | Relatório do período por competência | Página HTML |
| GET | `/reports.csv?start=AAAA-MM-DD&end=AAAA-MM-DD` | Exporta movimentações do período | CSV UTF-8 com BOM |
| GET | `/reports.pdf?start=AAAA-MM-DD&end=AAAA-MM-DD` | Exporta resumo e movimentações do período | PDF |
| GET | `/members` | Lista membros e perfis (só proprietário) | Página HTML; `403` para outros perfis |
| POST | `/members` | Adiciona membro: `full_name`, `email`, `password` (inicial; ignorada se o e-mail já tem conta), `role` (`owner`, `finance`, `viewer`) | Redireciona com mensagem; `400` com motivo |
| POST | `/members/:membership_id` | `action=role` + `role`, `action=deactivate` ou `action=activate` | Redireciona; `400` ao tentar remover o último proprietário |
| GET | `/integrations` | Página dos contratos demonstrativos | Página HTML |
| POST | `/integrations/simulate` | Executa simulador Pix, boleto, cartão, webhook ou Open Finance | Resultado fictício em HTML |

Limites do importador: não processa OFX 2.x XML, múltiplas contas por arquivo, conversão de moeda, nem arquivos binários multipart. O arquivo deve ser associado explicitamente a uma conta; todos os valores devem ser BRL.

Para integrar um provedor externo, siga [o guia de integrações](external-integrations.md). Ele exige contrato definido, credenciais via ambiente, sandbox e proteções de idempotência/webhook antes de qualquer ativação.


## Páginas web de títulos (cadastro server-rendered)

Títulos são separados de `/transactions`. As rotas abaixo usam sessão autenticada e token CSRF nos formulários POST:

| Método | Rota | Ação |
|---|---|---|
| GET | `/payables` | Lista títulos a pagar, com busca, situação e período de vencimento |
| POST | `/payables` | Cadastra título a pagar e suas parcelas |
| GET | `/receivables` | Lista títulos a receber com os mesmos filtros |
| POST | `/receivables` | Cadastra título a receber e suas parcelas |
| POST | `/titles/{id}/settlements` | Registra baixa parcial ou final, ajustes e comprovante; cria uma movimentação vinculada |
| POST | `/titles/{id}/cancel` | Cancela título em aberto que não tenha baixas |

O cadastro recebe contraparte, descrição, valor total, emissão/vencimento inicial, conta, categoria opcional, documento, condição/parcelas, forma de pagamento, centro de custo/projeto e referências opcionais específicas do tipo. Cada baixa requer chave de operação, valor aplicado, data, conta e forma; desconto, juros, multa, tarifas, comprovante e observações são opcionais. O saldo é derivado das baixas. Referências de anexo, cobrança e comprovante não carregam nem armazenam arquivos. Não existe API JSON de títulos nesta versão.

## Autorização por perfil

Toda requisição `POST`, `PUT`, `PATCH` ou `DELETE` autenticada passa por um middleware que confere o perfil contra a área da rota (exceto `/login`, `/register` e `/logout`). Rotas de cadastros de produto (`/products`, `/product-settings`, `/api/v1/products`) e de estoque (`/stock`, `/api/v1/stock`) exigem `owner` ou `stock`; compras (`/purchases`, `/api/v1/purchase-orders`) exigem `owner` ou `purchasing`; vendas e CRM (`/sales`, `/api/v1/sales-orders`, `/crm`, `/api/v1/crm`) exigem `owner` ou `sales`; contatos (`/contacts`) aceitam `owner`, `finance`, `purchasing` e `sales`; todas as outras exigem `owner` ou `finance`. Perfil `viewer` e perfis de outra área recebem `403`; na API, o corpo é `{"error": {"code": "forbidden", "message": "..."}}`.
