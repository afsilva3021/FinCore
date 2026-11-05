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
