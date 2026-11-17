# Requisitos do FinCore

## Produto

Aplicação web completa em pt-BR, com páginas renderizadas no servidor usando Robyn e templates HTML, Bootstrap, CSS e JavaScript. A API versionada dá suporte às integrações e aos consumidores externos; não é a interface principal do produto.

## Fase 1 — Fundação

## Histórias de usuário

- Como desenvolvedor, quero instalar as dependências com `uv sync` e iniciar a API com um comando documentado.
- Como operador local, quero subir um PostgreSQL isolado pelo Compose usando credenciais configuráveis em `.env`.
- Como operador, quero consultar `/health` e saber se a aplicação e o banco estão disponíveis.
- Como desenvolvedor, quero gerar e aplicar migrations versionadas do Tortoise ORM.

## Regras e critérios de aceite

- O servidor usa Robyn e expõe `GET /health` com JSON e status 200 quando o banco responde, ou 503 quando não responde.
- PostgreSQL é a persistência de desenvolvimento; credenciais vêm do ambiente e `.env` fica fora do Git.
- A verificação de saúde executa uma consulta leve e não revela credenciais nem detalhes internos ao cliente.
- O comando de migration usado deve existir na versão resolvida do Tortoise ORM.
- A fundação inclui configuração Ruff, pytest e testes HTTP/unitários iniciais.

## Fora de escopo nesta fase

Autenticação, organizações, lançamentos, plano de contas, integrações bancárias, dashboard e relatórios.

## Fase 2 — Identidade

- Como novo usuário, posso criar uma organização e uma conta de proprietário.
- Como usuário cadastrado, posso entrar e sair por páginas HTML.
- Senhas são armazenadas com Argon2id; sessões têm validade limitada e cookies HttpOnly/SameSite.
- Formulários que alteram estado validam CSRF; páginas privadas exigem sessão válida.
- Consultas de negócio devem sempre incluir a organização da sessão para impedir mistura de dados.

## Experiência da aplicação

- Páginas funcionam em telas móveis e desktop, com mensagens em pt-BR, estados vazios e navegação clara.
- Funcionalidade simulada ou ainda não liberada deve ser identificada; não apresentar indicadores fictícios como dados reais.
- Módulos planejados: gestão financeira, contabilidade, conciliação, dashboard e relatórios. Pix, boletos, cartões, webhooks e Open Finance permanecem em sandbox até haver provedor e requisitos definidos.

## Fase 8 — Cadastros de produto (ERP)

### Histórias de usuário

- Como responsável pelo estoque, quero cadastrar produtos com código, descrição, unidade, grupo, preço de venda e estoque mínimo para usá-los em estoque, compras e vendas.
- Como responsável pelo estoque, quero organizar os produtos em grupos e definir unidades de medida e depósitos da minha empresa.
- Como proprietário, quero dar a alguém acesso para manter os cadastros de produto sem que essa pessoa altere o financeiro.

### Critérios de aceite

- SKU duplicado na mesma organização é recusado, inclusive em envios simultâneos; outra organização pode usar o mesmo SKU.
- Código de barras com dígito verificador errado é recusado; quando informado, é único na organização.
- Estoque mínimo fracionado só é aceito em unidade que permite casas decimais.
- Grupo não pode ser subgrupo de si mesmo nem de um descendente.
- A organização tem sempre exatamente um depósito padrão; o padrão não pode ser inativado.
- Produto, grupo, unidade e depósito de uma organização não são lidos nem alterados por outra.
- Perfil Estoque altera cadastros de produto e só consulta o financeiro; Financeiro, o inverso; Somente leitura não altera nada.

## Fase 9 — Estoque (ERP)

### Histórias de usuário

- Como responsável pelo estoque, quero registrar o saldo inicial, entradas, saídas e transferências entre depósitos para saber quanto tenho e quanto vale.
- Como responsável pelo estoque, quero ver o histórico de cada produto e estornar um lançamento errado sem apagar nada.
- Como proprietário, quero ver o valor do estoque e os produtos abaixo do mínimo.
- Como contador, quero que os ajustes de estoque apareçam no diário e que a conta de estoque bata com as posições.

### Critérios de aceite

- A posição de cada produto em cada depósito é igual à soma dos movimentos, em quantidade e em valor.
- Saída ou transferência maior que o saldo é recusada, inclusive em envios simultâneos, e nada é gravado.
- Reenviar a mesma operação não movimenta o estoque duas vezes.
- Transferência move quantidade e o mesmo valor entre depósitos e não gera lançamento contábil.
- Movimento gravado não é alterado nem apagado, nem direto no banco; o estorno cria o movimento oposto e preserva o original.
- Ajustes geram lançamento com débitos iguais aos créditos; o estorno gera o lançamento inverso.
- Quantidades respeitam as casas decimais da unidade do produto.
- Só os perfis Proprietário e Estoque movimentam; movimentos de uma organização não são vistos nem alterados por outra.

## Fase 10 — Compras (ERP)

### Histórias de usuário

- Como comprador, quero registrar um pedido para um fornecedor e receber a mercadoria de uma vez ou em partes.
- Como financeiro, quero que cada recebimento gere as contas a pagar com as parcelas combinadas.
- Como contador, quero a mercadoria no ativo e a dívida no passivo no momento do recebimento.

### Critérios de aceite

- Não se recebe mais do que falta do pedido, nem em envios simultâneos.
- Recebimento grava estoque, títulos e diário juntos, ou nada.
- As parcelas somam exatamente o valor recebido.
- Pagar um título de compra reduz Fornecedores, não gera despesa.
- Estorno de recebimento restaura estoque, cancela títulos e inverte o diário; é recusado se houver baixa ou se a mercadoria já saiu.
- Pedido com mercadoria recebida não é cancelado.
- Pedidos e recebimentos de uma organização não são vistos nem alterados por outra.
