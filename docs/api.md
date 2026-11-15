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
- `POST /transactions/:transaction_id/reverse` — cria movimento compensatório e preserva o original; recusa a movimentação de uma baixa de título (estorne a baixa) e o estorno de um estorno.
- `GET /accounting` — plano de contas e diário.
- `POST /accounting/accounts`, `POST /accounting/journals` — contas contábeis e rascunhos.
- `POST /accounting/journals/:entry_id/update`, `/post`, `/reverse` — altera rascunhos, efetiva ou cria estorno. Lançamento gerado por movimentação e lançamento de estorno não aceitam estorno aqui.
- `GET /accounting/statements?start=AAAA-MM-DD&end=AAAA-MM-DD` — balancete de verificação, DRE e balanço patrimonial.
- `POST /accounting/period-lock` — `closed_until` (vazio reabre); só o proprietário, demais perfis recebem `403`.
- `GET /static/*` — CSS e JavaScript da interface.
- `GET /contacts`, `/customers`, `/suppliers` — contatos, clientes ou fornecedores; filtros `role` e `status` são aplicados na tela.
- `GET /contacts/:contact_id/edit` — formulário lateral de edição do contato autenticado.
- `POST /contacts`, `POST /contacts/:contact_id/update` — cria ou atualiza cliente/fornecedor, com CSRF.
- `POST /contacts/:contact_id/active` — inativa ou reativa contato; dados e vínculos históricos são preservados.
- `GET /contacts/postal-code?cep=01001000` — consulta ViaCEP, exige sessão e retorna endereço em JSON; erros upstream são controlados.

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
| POST | `/api/v1/titles/{id}/settlements/{settlement_id}/reverse` | JSON `{operation_key,reversed_on?}` | `200` com a baixa (`reversed_at`, `reversal_transaction_id`); devolve o saldo ao título e cria movimentação compensatória; repetir não tem efeito; período fechado = `409` |
| POST | `/api/v1/titles/{id}/cancel` | sem payload | `200` com o título cancelado; com baixa ativa ou já liquidado = `409` |
| GET | `/api/v1/crm/summary` | nenhum | Indicadores: leads por situação e conversão, negociações abertas, previsão ponderada, taxa de ganho, ciclo médio, paradas, atividades pendentes/atrasadas |
| GET | `/api/v1/crm/stages` | nenhum | `{items:[{id,name,position,win_probability,is_active}]}` |
| GET | `/api/v1/crm/leads` | `limit`, `offset`, `status=new\|contacted\|qualified\|disqualified\|converted\|active` | `{items:[lead],limit,offset,total}` |
| POST | `/api/v1/crm/leads` | JSON `{operation_key,name,company_name?,email?,phone?,source?,estimated_value?,notes?}` | `201` com o lead; reenvio com a mesma chave devolve o mesmo lead |
| GET | `/api/v1/crm/leads/{id}` | UUID do lead | Lead ou `404` |
| POST | `/api/v1/crm/leads/{id}/convert` | JSON `{counterparty_id?,person_type?,tax_id?,create_deal?,deal_title?,deal_amount?,stage_id?,expected_close_date?}` | `201 {lead,counterparty_id,deal}`; já convertido ou contato duplicado = `409` |
| GET | `/api/v1/crm/deals` | `limit`, `offset`, `status=open\|won\|lost` | `{items:[negociação],limit,offset,total}` |
| POST | `/api/v1/crm/deals` | JSON `{operation_key,counterparty_id,title,amount?,stage_id?,expected_close_date?,notes?}` | `201` com a negociação; reenvio idempotente |
| GET | `/api/v1/crm/deals/{id}` | UUID da negociação | Negociação ou `404` |
| POST | `/api/v1/crm/deals/{id}/stage` | JSON `{stage_id}` | `200`; negociação encerrada = `409` |
| POST | `/api/v1/crm/deals/{id}/win` | sem payload | `200` (repetir numa negociação ganha devolve a mesma) |
| POST | `/api/v1/crm/deals/{id}/lose` | JSON `{lost_reason}` | `200`; sem motivo = `400` |
| GET | `/api/v1/crm/activities` | `limit`, `offset`, `status=pending\|done\|all` | `{items:[atividade],limit,offset,total}` |
| POST | `/api/v1/crm/activities` | JSON `{type,subject,due_date?,notes?,done?}` e exatamente um de `lead_id`, `deal_id`, `counterparty_id` | `201` com a atividade |
| POST | `/api/v1/crm/activities/{id}/complete` | sem payload | `200` com `done_at` |
| GET | `/api/v1/crm/proposals` | `limit`, `offset`, `status=draft\|sent\|accepted\|rejected` | `{items:[proposta com total e expired],limit,offset,total}` |
| POST | `/api/v1/crm/deals/{id}/proposals` | JSON `{operation_key,items:[{description,quantity?,unit_price}],valid_until,discount?,notes?}` | `201` com a proposta em rascunho, `items[]` e `titles[]` vazio |
| GET | `/api/v1/crm/proposals/{id}` | UUID da proposta | Proposta com `items[]` e `titles[]` |
| POST | `/api/v1/crm/proposals/{id}/send` | sem payload | `200`; só registra a apresentação, nada é enviado |
| POST | `/api/v1/crm/proposals/{id}/reject` | JSON `{reason?}` | `200` |
| POST | `/api/v1/crm/proposals/{id}/accept` | JSON `{operation_key,account_id,payment_method,first_due_date,installments?,category_id?}` | `201` com `titles[]` (títulos a receber gerados); reenvio devolve os mesmos; proposta não enviada, vencida ou recusada = `409` |

No CRM, `quantity` é string decimal com até três casas (`"1.5"`); valores monetários seguem a regra geral (`"250.00"`). O aceite de proposta não movimenta caixa: gera títulos, que são baixados por `/api/v1/titles/{id}/settlements`. Regras em [crm.md](crm.md).

A especificação OpenAPI 3.1 dos endpoints de sessão, de títulos e do CRM está em [`docs/openapi.yaml`](openapi.yaml) (validada com `openapi-spec-validator`).

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
| POST | `/integrations/simulate` | Executa simulador Pix, boleto, cartão, webhook, Open Finance, e-mail ou WhatsApp do CRM | Resultado fictício em HTML; nada é enviado |

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
| POST | `/titles/{id}/settlements/{settlement_id}/reverse` | Estorna a baixa (`operation_key`): reabre o saldo do título e compensa caixa e razão |
| POST | `/titles/{id}/cancel` | Cancela título em aberto que não tenha baixas ativas |

O cadastro recebe contraparte, descrição, valor total, emissão/vencimento inicial, conta, categoria opcional, documento, condição/parcelas, forma de pagamento, centro de custo/projeto e referências opcionais específicas do tipo. Cada baixa requer chave de operação, valor aplicado, data, conta e forma; desconto, juros, multa, tarifas, comprovante e observações são opcionais. O saldo é derivado das baixas. Referências de anexo, cobrança e comprovante não carregam nem armazenam arquivos. A API JSON equivalente está em `/api/v1/titles`.

## Páginas web do CRM

Sessão autenticada e token CSRF em todo POST. Erros de regra devolvem a própria página com o motivo e status `400`; IDs inexistentes ou de outra organização devolvem `404`.

| Método | Rota | Ação |
|---|---|---|
| GET | `/crm` | Painel comercial com os indicadores |
| GET, POST | `/crm/leads` | Lista (filtros `status`, `q`) e cadastra lead |
| POST | `/crm/leads/{id}/update` | Atualiza critérios de qualificação e situação |
| GET, POST | `/crm/leads/{id}/convert` | Formulário e conversão em contato/negociação |
| GET, POST | `/crm/deals` | Quadro por etapa (`status=open`) ou lista de ganhas/perdidas; cadastra negociação |
| GET | `/crm/deals/{id}` | Detalhe com atividades, propostas e histórico |
| POST | `/crm/deals/{id}/update`, `/stage`, `/win`, `/lose`, `/reopen` | Altera dados, move etapa, ganha, perde (com `lost_reason`) ou reabre |
| POST | `/crm/deals/{id}/activities` | Registra atividade na negociação |
| POST | `/crm/deals/{id}/proposals` | Cria proposta em rascunho (`item_description_N`, `item_quantity_N`, `item_price_N`, `discount`, `valid_until`) |
| GET, POST | `/crm/activities` | Lista (`status=pending\|done\|all`) e registra atividade |
| POST | `/crm/activities/{id}/complete` | Conclui atividade |
| GET, POST | `/crm/stages` | Lista e adiciona etapas do funil |
| POST | `/crm/stages/{id}/update` | Altera nome, ordem e probabilidade, ou `action=activate\|deactivate` |
| GET | `/crm/proposals` | Lista propostas (`status`) |
| GET | `/crm/proposals/{id}` | Detalhe com itens, totais e títulos gerados |
| POST | `/crm/proposals/{id}/send`, `/reject`, `/accept` | Marca como enviada, recusa ou registra o aceite (`account_id`, `payment_method`, `installment_count`, `first_due_date`, `category_id?`) e gera os títulos a receber |

## Autorização por perfil

Toda requisição `POST`, `PUT`, `PATCH` ou `DELETE` autenticada passa por um middleware que exige perfil `owner` ou `finance` (exceto `/login`, `/register` e `/logout`). Perfil `viewer` recebe `403`; na API, o corpo é `{"error": {"code": "forbidden", "message": "..."}}`.
