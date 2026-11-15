# Regras contábeis do FinCore

## Natureza e saldo normal

- **Ativo:** saldo normal devedor; aumenta com débitos e diminui com créditos.
- **Passivo:** saldo normal credor; aumenta com créditos e diminui com débitos.
- **Patrimônio líquido:** saldo normal credor.
- **Receita:** saldo normal credor.
- **Despesa:** saldo normal devedor.

O livro razão soma somente linhas de lançamentos efetivados. Rascunhos não alteram saldo. As linhas usam BRL e `NUMERIC(15,2)`.

## Lançamentos

- Um lançamento efetivado exige pelo menos duas linhas, valores não negativos, uma coluna (débito ou crédito) preenchida por linha e soma de débitos igual à soma de créditos.
- O lançamento permanece editável enquanto rascunho. A efetivação bloqueia alterações; a API de aplicação não oferece edição de itens efetivados.
- Estornos financeiros criam movimento oposto vinculado ao original e lançamento contábil compensatório, mantendo o histórico intacto.
- Estorno cria um novo lançamento efetivado com débitos e créditos invertidos e referência ao original. O original continua preservado e não pode receber mais de um estorno.
- Efetivação e estorno usam transação PostgreSQL e bloqueio da linha do cabeçalho para evitar transições concorrentes.
- Chaves idempotentes únicas evitam duplicação de rascunhos por reenvio do formulário.

## Vínculo com o núcleo financeiro

Cada conta financeira e categoria recebe uma conta contábil associada. Categorias sem classificação usam contas gerais da organização. Movimentações liquidadas geram um lançamento efetivado com vínculo único à transação: receitas debitam ativo e creditam receita; despesas debitam despesa e creditam ativo; saldos iniciais debitam ativo e creditam patrimônio. O comando `fincore-backfill-accounting` cria os vínculos contábeis ausentes para movimentos históricos liquidados e pode ser repetido com segurança.

## Transferências financeiras

O núcleo financeiro registra transferência como duas linhas internas de conta (origem e destino), na mesma transação PostgreSQL e com o mesmo identificador de transferência. Um único lançamento contábil debita a conta contábil de destino e credita a conta de origem. Isso preserva o total e não cria receita ou despesa.

## Plano inicial

O usuário define códigos e contas da própria organização. O sistema não declara um plano regulatório ou específico para qualquer setor. Naturezas de conta são classificações gerais para cálculo do saldo normal.

## Estrutura sintética e analítica

- Contas contábeis **sintéticas** são grupos que podem conter subcontas, acumulam o saldo normal das contas descendentes e não aceitam partidas diretamente.
- Contas **analíticas** são folhas do plano e podem receber linhas de diário.
- A conta superior precisa pertencer à organização e ter a mesma natureza contábil. Apenas grupos sintéticos podem ser selecionados como superiores.
- Categorias financeiras também aceitam grupos sintéticos e subcategorias analíticas. Uma subcategoria precisa pertencer a um grupo sintético do mesmo tipo (receita ou despesa); movimentações só aceitam categorias analíticas.
- Categorias analíticas mantêm o vínculo com suas contas de receita/despesa no razão. Os grupos sintéticos agregam os saldos contábeis das contas filhas.

## Regime contábil

O FinCore usa **regime de caixa**: receita e despesa entram no razão quando a movimentação é liquidada ou o título recebe baixa. Cadastrar um título não gera lançamento. ERPs como ERPNext e Odoo usam regime de competência (a fatura já lança contas a receber/pagar); essa diferença é deliberada e está registrada em `docs/decisions.md`. Os títulos em aberto aparecem em Contas a pagar/receber, não no balanço.

## Baixa de título: uma conta para cada natureza

A baixa gera um único lançamento com o principal, os encargos e os abatimentos em contas separadas. O banco movimenta o valor líquido.

| | Débito | Crédito |
|---|---|---|
| **A receber** | Banco (líquido) · Descontos concedidos · Tarifas bancárias | Receita da categoria (valor aplicado) · Receitas financeiras (juros + multa) |
| **A pagar** | Despesa da categoria (valor aplicado) · Despesas financeiras (juros + multa) · Tarifas bancárias | Banco (líquido) · Descontos obtidos |

Contas de sistema criadas no primeiro uso: `SYS-FIN-REV`, `SYS-FIN-EXP`, `SYS-DISC-GIVEN`, `SYS-DISC-RECV` e `SYS-BANK-FEES`. Baixas anteriores a esta regra continuam como foram lançadas (valor líquido inteiro na conta da categoria); lançamentos efetivados não são reescritos.

## Estornos

- **Estorno espelhado:** o estorno de uma movimentação inverte as mesmas contas do lançamento original e fica ligado a ele (`reversal_of`). Antes, o estorno de uma receita debitava "Despesas sem categoria", deixando a receita da categoria inflada.
- **Estorno de baixa:** a movimentação gerada por uma baixa só é estornada pelo estorno da baixa (em Contas a pagar/receber ou `POST /api/v1/titles/{id}/settlements/{settlement_id}/reverse`). Numa transação: cria a movimentação compensatória com lançamento espelhado, marca a baixa como estornada (ela permanece no histórico) e devolve o valor ao saldo do título, que volta a "parcial" ou "em aberto". Repetir o estorno não tem efeito.
- **Estorno não é estornado:** nem movimentação nem lançamento de estorno aceitam novo estorno; corrige-se com um lançamento novo.
- **Lançamento gerado por movimentação** não é estornado direto no diário: estornar só o razão deixaria o saldo da conta financeira diferente do saldo contábil. Estorna-se a movimentação ou a baixa de origem.
- Título cuja única baixa foi estornada pode ser cancelado; a baixa estornada continua registrada.

## Fechamento de período

O proprietário define em `/accounting` a data até a qual o período está fechado (inclusive). Com o período fechado, nenhum lançamento é efetivado com data até esse dia — manual, de movimentação, de baixa ou de estorno; a operação inteira é recusada e nada fica pela metade. Guarda-se quem alterou e quando. Reabrir é limpar a data.

Exceção controlada: liquidar hoje uma movimentação em aberto cuja competência caiu em período fechado lança na data da liquidação, para não reabrir o período.

## Demonstrativos (`/accounting/statements`)

Calculados das linhas de lançamentos efetivados; não há saldo gravado.

- **Balancete de verificação:** por conta analítica, saldo anterior, débitos e créditos do período e saldo final; o total de débitos deve ser igual ao de créditos.
- **DRE:** movimento do período das contas de receita menos o das contas de despesa.
- **Balanço patrimonial** na data final: Ativo = Passivo + Patrimônio líquido + Resultado acumulado. O resultado acumulado (receitas − despesas) aparece como linha própria; não há lançamento de encerramento de exercício.

## Diferenças conhecidas em relação a ERPs

- Regime de caixa, sem contas a receber/pagar no razão e sem provisões.
- Sem impostos, retenções, centros de custo contábeis, multimoeda ou ativo imobilizado.
- Sem encerramento de exercício com transferência do resultado para lucros acumulados.
- Lançamentos manuais podem usar a conta contábil de uma conta financeira; isso altera o razão sem criar movimentação, e o saldo financeiro deixa de bater com o contábil. Use movimentações para tudo que envolve banco ou caixa.
- A data contábil de uma movimentação é a data de competência informada; não há data de liquidação separada no lançamento, exceto no caso de período fechado.
- Conciliação exige data e valor idênticos, sem tolerância.
