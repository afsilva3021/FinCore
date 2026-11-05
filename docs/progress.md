# Progresso

## Fase 1 — Fundação

- [x] Configuração inicial do projeto, Python, `uv`, Ruff e pytest.
- [x] Serviço PostgreSQL via Docker Compose com credenciais por ambiente e healthcheck coerente.
- [x] Inicialização Robyn, ciclo de vida do Tortoise ORM e endpoint `/health`.
- [x] Endpoints JSON versionados v1 para saúde, contas e consulta paginada de transações.
- [x] API v1 com criação autenticada de contas, categorias e movimentações, liquidação e estorno.
- [x] Teste PostgreSQL/HTTP de API v1 verifica CSRF, idempotência, liquidação e ator da auditoria.
- [x] Registrar o usuário que efetivou a liquidação com migration compatível com registros existentes.
- [x] Requisitos, decisões e contrato inicial documentados.
- [x] Gerar lockfile e conferir os comandos de migration na versão resolvida.
- [x] Criar CI e testes básicos da API.
- [x] Validar migrations com PostgreSQL disponível e `/health` com resposta 200.

## Fase 2 — Identidade e páginas web

- [x] Templates Bootstrap pt-BR de login, cadastro e dashboard inicial.
- [x] Cadastro de organização/usuário, hash Argon2id, sessão assinada, logout e CSRF.
- [x] Migration inicial de identidade.
- [x] Validar migration e cadastro/login/logout em HTTP real contra PostgreSQL de teste.
- [x] Demonstrar isolamento por organização em testes de persistência.

## Fase 3 — Núcleo financeiro

- [x] Contas, categorias, receitas, despesas, transferências, liquidação e extratos internos.
- [x] Saldos derivados de movimentações liquidadas, sem campo de saldo editável.
- [x] Páginas de cadastro/listagem em Bootstrap, com autorização por organização.
- [x] Estornos financeiros compensatórios, com referência ao original e suporte a transferências.
- [x] Validar estorno financeiro por HTTP sem apagar o original.
- [x] Testes unitários e de persistência para dinheiro, liquidação, transferências, idempotência e concorrência.
- [x] Validação HTTP real de cadastro/login, páginas financeiras, dashboard, liquidação e assets.

## Fase 4 — Contabilidade

- [x] Plano de contas manual, diário, partidas dobradas, ledger e estornos compensatórios.
- [x] Imutabilidade após efetivação e bloqueio de transições concorrentes.
- [x] Regras de natureza das contas documentadas em `docs/accounting.md`.
- [x] Gerar lançamentos automaticamente para movimentações liquidadas e transferências, com vínculo idempotente.
- [x] Adicionar comando de backfill idempotente para registros liquidados anteriores à integração contábil.
- [x] Validar a interface contábil por HTTP real e regras de persistência em PostgreSQL.

## Fase 5 — Conciliação

- [x] Parser OFX 1.x SGML com validação de data, valor, FITID e duplicidade no arquivo.
- [x] Importação vinculada à organização e conta com chave única por conta/FITID.
- [x] Revisão manual de conciliação por conta, data e valor, sem alterar o financeiro.
- [x] Registro auditável de conciliar/desfazer conciliação e página Bootstrap dedicada.
- [x] Validar cadastro, páginas, importação OFX e login por HTTP real.
- [x] Validar conciliar/desfazer pelo formulário HTTP; o serviço e auditoria passam no PostgreSQL.
- [x] Fixture documentada e testes unitários e PostgreSQL para idempotência/vínculo.

## Fase 6 — Relatórios e apresentação

- [x] Relatório de movimentações por período com receitas/despesas realizadas.
- [x] Exportação CSV e PDF com dados isolados por organização.
- [x] Validar páginas e download PDF/CSV por HTTP real.

## Fase 7 — Integrações simuladas

- [x] Adapters internos para Pix, boleto, cartão, webhook e Open Finance em modo simulado.
- [x] Página Bootstrap sinaliza simulações e não envia dados nem pagamentos.
- [x] Testes unitários dos cenários simulados.
- [x] Validar a página de simulações por HTTP real.

## Próximo passo

Fases 1–7 validadas para o escopo implementado, com API v1 de escrita validada por HTTP. Pendências: CNAB depende de banco e versão/layout; pagamentos e Open Finance continuam simulados. O guia de integração externa está em `docs/external-integrations.md`.

## Aprimoramento — Contas a pagar e a receber

- [x] Criadas telas próprias de cadastro e consulta para títulos a pagar e a receber.
- [x] Títulos, contatos e baixas têm persistência própria e são isolados por organização.
- [x] Cadastro suporta documento, categoria analítica, conta, centro de custo/projeto, datas, forma/condição, parcelamento, observações e referências específicas por tipo.
- [x] Baixas parciais/finais registram valor aplicado, ajustes, líquido, conta, data e comprovante; criam movimento contábil vinculado atomicamente.
- [x] Títulos sem baixa não aparecem no extrato nem alteram saldos; vencimento é calculado pela data e saldo.
- [x] Migration `0012` adiciona tabelas próprias e preserva os registros existentes sem conversão presumida.
- [x] Migrations `0012` e `0013` aplicadas sem remover registros; lint e testes de integração dos títulos aprovados.

## UX — tela de transações

- [x] Extrato interno passou a ocupar o topo da tela, sem formulário de cadastro inline.
- [x] Cadastro de movimento aberto por drawer de 480 px no desktop e tela completa no celular.
- [x] Drawer mantém os campos atuais, restaura valores após erro de validação, contém foco e pede confirmação ao fechar com alterações.
- [x] Preferência de tema claro/escuro acessível no cabeçalho e persistida no navegador.
- [x] Interface ajustada para acentos azul claro.
- [x] Confirmações de saída não salva e estorno são exibidas no modal Bootstrap compartilhado, sem diálogos nativos do navegador.
- [x] Ruff, sintaxe JavaScript e suíte completa de testes validados.

## Visão geral com dados persistidos

- [x] Removidos os indicadores, aplicativos, licenças e renovações de demonstração da página inicial.
- [x] Visão geral exibe saldo, receitas e despesas liquidadas, itens em aberto, movimentações recentes e saldos por conta consultados no PostgreSQL.
- [x] Estados vazios orientam o cadastro quando a organização ainda não tem contas ou movimentações.

## Categorias e plano hierárquico

- [x] Categorias sintéticas agrupam subcategorias analíticas de receita ou despesa.
- [x] Movimentações aceitam somente categorias analíticas.
- [x] Plano contábil permite contas superiores sintéticas e folhas analíticas; partidas rejeitam grupos e saldos sintéticos totalizam as descendentes.
- [x] Migration aditiva mantém categorias e contas existentes como analíticas.
- [x] Contratos da API e regras contábeis documentados.
