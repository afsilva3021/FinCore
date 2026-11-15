# CRM do FinCore

O CRM cobre o ciclo comercial antes do financeiro: **lead → contato → negociação → proposta → título a receber**. Ele reaproveita o cadastro de contatos (`Counterparty`) e termina em Contas a receber; não duplica entidades financeiras.

As regras puras ficam em `src/fincore/domain/crm.py` (testadas sem banco em `tests/unit/test_crm_rules.py`); os casos de uso em `application/crm.py` e `application/crm_proposals.py`; as leituras e os indicadores em `application/queries.py`.

## Referências de comportamento

Regras inspiradas no comportamento público de EspoCRM, SuiteCRM, Twenty, Odoo CRM, ERPNext, Zoho CRM, RD Station CRM e Pipedrive (entidades, conversão de lead, funil com probabilidade, motivo de perda, negociação parada, indicadores). Nenhum código de terceiros foi copiado. A pesquisa usou resumos de busca e comparativos, não a documentação oficial completa de cada produto; trate as regras abaixo como decisões do FinCore, não como compatibilidade com esses produtos.

## Entidades

| Modelo | Papel |
|---|---|
| `CrmPipelineStage` | Etapa do funil por organização: nome, ordem e probabilidade de ganho (0–100%) |
| `CrmLead` | Contato comercial ainda não qualificado; guarda origem, valor estimado e critérios de qualificação |
| `CrmDeal` | Negociação com um contato (`Counterparty`), numa etapa, com valor e previsão de fechamento |
| `CrmDealEvent` | Trilha de auditoria da negociação: quem criou, moveu, ganhou, perdeu ou reabriu, e quando |
| `CrmActivity` | Tarefa, ligação, reunião, e-mail ou nota ligada a **exatamente um** lead, negociação ou contato |
| `CrmProposal`, `CrmProposalItem` | Proposta numerada por organização, com itens, desconto e validade |

Não há hierarquia de empresa/pessoa: o contato é uma pessoa física ou jurídica, como no restante do sistema.

## Leads

Situações: `new` (novo), `contacted`, `qualified`, `disqualified`, `converted`.

```text
new ──► contacted ◄──► qualified
 │          │              │
 └──────────┴──────────────┴──► disqualified ──► new (reabrir)
qualquer situação viva ──► converted (só pela conversão; final)
```

- **Qualificação:** quatro critérios (necessidade, orçamento, fala com o decisor, prazo). Para marcar `qualified` é preciso necessidade confirmada e ao menos mais um critério.
- **Desqualificar** exige motivo.
- **Conversão** (atômica, numa transação):
  1. o lead vira um contato novo **ou** é vinculado a um contato existente, que passa a ser cliente;
  2. opcionalmente nasce uma negociação na etapa escolhida, com o valor estimado do lead;
  3. o lead fica `converted`, com contato, negociação, usuário e horário registrados.
- **Duplicidade:** ao criar contato novo, a conversão é recusada se já existir contato da organização com o mesmo e-mail ou o mesmo nome (sem diferenciar maiúsculas). O usuário deve vincular ao existente.
- Lead convertido é imutável. Converter não cria título nem movimenta caixa.

## Funil e negociações

- Etapas padrão criadas no primeiro uso: Prospecção 10%, Qualificação 25%, Proposta 50%, Negociação 75%. Editáveis em `/crm/stages` (até 12).
- **Ganha e perdida são situações da negociação, não etapas.** Situações: `open`, `won`, `lost`.
- Só negociação `open` muda de etapa ou de dados. Perder exige motivo. Encerrada pode ser reaberta, exceto se tiver proposta aceita.
- Etapa com negociação em aberto não pode ser desativada; o funil mantém ao menos uma etapa ativa.
- **Parada:** negociação aberta sem mudar de etapa há mais de 14 dias (`STALLED_AFTER_DAYS`). É condição calculada, não um estado.
- Cada mudança grava um `CrmDealEvent` com usuário e horário (UTC).

## Propostas e a ponte com o financeiro

Situações: `draft`, `sent`, `accepted`, `rejected`. **Vencida** é condição calculada (rascunho ou enviada com validade no passado).

```text
draft ──► sent ──► accepted (gera títulos a receber; final)
  │         │
  └─────────┴──► rejected (final)
```

- Total = soma das linhas (quantidade × preço unitário, cada linha arredondada para centavos, meio para cima) − desconto. Não há campo de total editável.
- "Enviar" apenas registra que a proposta foi apresentada. **O FinCore não envia e-mail nem mensagem.**
- **Aceite** (uma transação PostgreSQL, com bloqueio da proposta e da negociação):
  1. cria títulos a receber (`FinancialTitle`) para o contato da negociação, à vista ou em até 36 parcelas mensais que somam exatamente o total;
  2. marca a proposta `accepted` e guarda a primeira parcela em `first_title`;
  3. atualiza o valor da negociação para o total aceito e a marca como ganha.
- **O aceite não movimenta caixa nem aparece no extrato.** O dinheiro só entra quando o título recebe baixa em Contas a receber, que então cria a transação e o lançamento contábil — a regra de títulos do `AGENTS.md` continua valendo.
- Reenviar o aceite de proposta já aceita devolve os mesmos títulos (idempotente, inclusive em envios simultâneos).
- Uma negociação aceita **uma única proposta**: com uma já aceita, o aceite de outra é recusado, para não gerar títulos em dobro.
- A `operation_key` do aceite vira a chave de criação dos títulos; se ela já pertence a outros títulos, o aceite é recusado com conflito.

## Indicadores (`/crm` e `GET /api/v1/crm/summary`)

| Indicador | Cálculo |
|---|---|
| Conversão de leads | leads convertidos ÷ leads cadastrados |
| Previsão ponderada | Σ (valor das negociações abertas da etapa × probabilidade da etapa) |
| Taxa de ganho | ganhas ÷ (ganhas + perdidas) |
| Ciclo médio de venda | média de dias entre criação e ganho (últimas 500 ganhas) |
| Negociações paradas | abertas sem mudar de etapa há mais de 14 dias |

Previsão e valor ganho são números comerciais: **não são receita realizada nem saldo**. O realizado continua na visão geral financeira.

## Autorização e isolamento

- Todas as consultas filtram pela organização da sessão; IDs de outra organização respondem `404` e não alteram nada (`tests/integration/test_cross_organization_isolation.py`).
- Perfil somente leitura consulta o CRM, mas toda escrita responde `403` pelo middleware global.
- Chaves de idempotência de outra organização respondem `409`, nunca devolvem o registro alheio.

## Limites atuais

- Sem envio de e-mail, WhatsApp, campanhas ou captura automática de leads; há apenas simuladores identificados em `/integrations`.
- Sem funis múltiplos, automações, campos personalizados, importação de leads ou responsável diferente do criador.
- O quadro move negociações por seletor (sem arrastar e soltar) e mostra até 300 negociações abertas.
- Propostas não geram PDF e não são editadas depois de criadas; para alterar, descarte e crie outra.
