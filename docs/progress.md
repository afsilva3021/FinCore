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

<<<<<<< HEAD
## Cadastros e consistência de interface

- [x] Cadastro de clientes/fornecedores com CPF/CNPJ, endereço, contato, edição, filtro e inativação/reativação.
- [x] Busca de CEP por ViaCEP no cadastro, com preenchimento editável e erros controlados.
- [x] Contatos ativos reutilizáveis na criação de contas a pagar/receber; títulos antigos continuam compatíveis.
- [x] Cadastro de contato isolado por organização; migration `0014` preserva e classifica contatos existentes.
- [x] Categorias, contas contábeis/lançamentos e importação OFX seguem o padrão de botão e drawer lateral já usado em transações e títulos.
- [x] JavaScript compartilhado suporta vários drawers, foco, Escape, backdrop e confirmação ao fechar formulários alterados.
- [x] Validados Ruff, sintaxe JavaScript, testes unitários e integração com PostgreSQL isolado.

## UX — cadastro de subcategorias

- [ ] A tela Categorias oferece ação direta para criar uma subcategoria sob o grupo sintético escolhido, com tipo e classificação analítica pré-selecionados; validação automatizada pendente.
=======
## Correções de revisão (2026-11-05)

- [x] Valores em pt-BR: ponto separando grupos de 3 dígitos é milhar (`1.500` = R$ 1.500,00); formatos ambíguos são rejeitados. Parsers de formulário centralizados em `application/money.py`; a API mantém a notação decimal canônica (`"250.00"`).
- [x] Token CSRF dura o mesmo que a sessão (12 h) e é rotacionado no login/cadastro; antes, formulários falhavam em silêncio depois de 1 h.
- [x] Totais de receitas e despesas (visão geral e relatórios) abatem estornos da natureza do movimento original e são agregados no banco.
- [x] Baixa e cancelamento de título exibem o motivo real da recusa na página do tipo correto.
- [x] Cadastro exige confirmação de senha e aplica política de força no servidor (com retorno imediato no navegador); campos preenchidos são preservados após erro, exceto as senhas.
- [x] Ruff e suíte completa (74 testes, com PostgreSQL de teste) aprovados.
- [x] Pendências da revisão concluídas em 2026-11-06 (segredo obrigatório, limite de login, revogação de sessão, headers, perfis, N+1 e `CHECK` constraints).
>>>>>>> 552a8c89c4c1ea56746cb0baacda645697a96af0

## Aplicação separada do banco (2026-11-06)

- [x] `Dockerfile` multi-stage com `uv`, usuário sem root e healthcheck em `/health`; `.dockerignore` sem `.env`, testes ou `.git`.
- [x] `compose.yaml`: serviços `db` (rede interna, sem porta no host), `migrate` (roda migrations e cria o usuário restrito) e `app` (rede interna + frontend, porta só em `127.0.0.1`, read-only, `cap_drop: ALL`).
- [x] `compose.dev.yaml` para publicar o banco em `127.0.0.1:5433` só em desenvolvimento.
- [x] `fincore-setup-app-db-role`: idempotente; usuário restrito não cria/apaga tabelas nem lê `tortoise_migrations` (verificado no PostgreSQL 16).
- [x] Suíte completa (95 testes) aprovada conectando com o usuário restrito; aplicação iniciada como usuário sem privilégios, com código somente leitura, `/health` ok.
- [x] Aplicação recusa iniciar exposta com `AUTH_SECRET` padrão/curto (testes unitários).
- [ ] Pendente de validação: `docker compose up --build` real (o ambiente da implementação não alcança os registries de imagem).
- [ ] Produção: proxy HTTPS, `SECURE_COOKIES=true`, cofre de segredos e backup — checklist no README.

## Segurança de autenticação (2026-11-06)

- [x] Bloqueio de login por e-mail (5 falhas) e por IP (20 falhas) em janela de 15 min, com upsert e `SELECT ... FOR UPDATE` contra corrida; `429` na página de login.
- [x] Verificação contra hash fictício para e-mail inexistente (sem enumeração por tempo).
- [x] `session_version` no usuário; logout revoga todos os tokens. Migration `0016` testada em banco com usuários existentes (coluna criada anulável, preenchida e então `NOT NULL`).
- [x] Headers de segurança globais; script inline do tema movido para `static/js/theme.js`. 11 páginas abertas no Chromium sem violações de CSP.

## Perfis de acesso e CI (2026-11-06)

- [x] Perfis `owner`, `finance` e `viewer`; middleware global nega escrita para `viewer` em HTML e API (testado com POST de conta nas duas interfaces).
- [x] `/members`: adicionar, mudar perfil, desativar/reativar; protege o último proprietário ativo; isolamento entre organizações testado.
- [x] Layout mostra o perfil real (antes, "Administrador" fixo) e aviso de somente leitura.
- [x] CI: job `docker` constrói a imagem, sobe o Compose com `--wait`, confere `/health`, `migrate` com código 0, banco sem porta no host e container sem root; job `quality` repete a suíte com o usuário restrito do banco.
- [ ] O job `docker` roda pela primeira vez no GitHub Actions (não foi possível construir a imagem no ambiente da implementação).

## Integridade, desempenho e isolamento (2026-11-06)

- [x] Migration `0017`: 17 `CHECK` constraints; validadas contra o banco de teste existente (todas `convalidated = true`) e testadas num banco antigo com linha inválida (migration conclui, constraint fica `NOT VALID` e bloqueia gravações novas).
- [x] `get_ledger_balances`, `get_account_balances`, diário contábil e conciliação sem N+1 (uma agregação/consulta por tela).
- [x] Paginação de 50 itens com `?page=N` em transações, títulos, contatos, diário e conciliação.
- [x] Teste de isolamento: organização B tenta 18 rotas com IDs da organização A (HTML e API); nada vaza nem muda. Execução de controle com o dono de A confirma que as mesmas requisições alteram dados.

## Arquitetura (2026-11-06)

- [x] `domain/` com regras puras (dinheiro, títulos, partidas dobradas) e 14 testes unitários sem banco.
- [x] `presentation/routes/` com um módulo por área; `web.py` ficou com sessão, CSRF, templates e paginação (683 linhas).
- [x] `login_required` e `api_login_required` substituem os blocos de verificação de sessão repetidos.
- [x] Nenhuma consulta ORM direta nas rotas: leitura em `application/queries.py`; conciliação sem N+1 de candidatos.
- [x] `tests/unit/test_architecture.py`: domínio sem framework/ORM e apresentação sem consulta ORM direta.
- [x] Servidor real iniciado e 12 telas navegadas no Chromium após a refatoração.

## API de títulos (2026-11-06)

- [x] `GET/POST /api/v1/titles`, `GET /api/v1/titles/{id}`, `POST .../settlements`, `POST .../cancel`.
- [x] Idempotência na criação (`FinancialTitle.operation_key`, migration `0018`; corrida resolvida devolvendo o que o outro envio criou) e nas baixas.
- [x] Correção de segurança: `settle_title` devolvia a baixa existente para qualquer chave repetida, inclusive de outra organização; agora só para o mesmo título e organização (senão `409`).
- [x] `docs/openapi.yaml` validado com `openapi-spec-validator`.

## Portfólio e sugestão de categoria (2026-11-06)

- [x] `domain/categorization.py` (Naive Bayes multinomial com suavização de Laplace, limiar de confiança e mínimo de exemplos) + `GET /api/v1/categories/suggestion`; formulário de transação preenche a categoria e mostra a confiança. Testado sem banco, com banco e no navegador ("Conta de luz Enel novembro" → Energia, 96%).
- [x] `fincore-seed-demo` com trava `FINCORE_ALLOW_DEMO_SEED=1` e recusa em segunda execução.
- [x] Screenshots em `docs/screenshots/` capturados no Chromium a partir do seed (desktop 2x, tema escuro e celular).
- [x] Corrigido: visão geral não exibia saldos por conta (variável `balances` nunca enviada); "Administrador" fixo no perfil; textos "até 100/200" após a paginação.
- [ ] Demo online: depende de hospedagem e domínio do responsável pelo projeto.

## CRM (2026-11-15)

- [x] `domain/crm.py`: transições de lead, qualificação, funil, valor ponderado, negociação parada, totais e estados de proposta, indicadores — 23 testes unitários sem banco.
- [x] Modelos `CrmPipelineStage`, `CrmLead`, `CrmDeal`, `CrmDealEvent`, `CrmActivity`, `CrmProposal`, `CrmProposalItem`; migration `0019` (gerada por `makemigrations`) e `0020` (15 `CHECK` constraints). Aplicadas no banco de teste; `makemigrations` responde `No changes detected`.
- [x] Leads: cadastro, qualificação, desqualificação com motivo e conversão atômica em contato/negociação com recusa de contato duplicado (testado: falha na conversão não deixa contato órfão).
- [x] Funil e negociações: etapas padrão e configuráveis, mover, ganhar, perder com motivo, reabrir, histórico por usuário; banco rejeita linhas inválidas.
- [x] Atividades ligadas a exatamente um registro, com conclusão idempotente.
- [x] Propostas: numeração sequencial sob concorrência, itens e desconto em `Decimal`, validade, envio (só registro), recusa e aceite.
- [x] Aceite gera títulos a receber que somam exatamente o total, sem `FinancialTransaction` nem `JournalEntry`; cinco aceites simultâneos geram os títulos uma única vez; falha no aceite não altera proposta nem negociação; a baixa posterior segue o fluxo financeiro normal.
- [x] Painel `/crm` e `GET /api/v1/crm/summary`: indicadores conferidos contra valores de referência e isolados por organização.
- [x] Páginas `/crm`, `/crm/leads`, `/crm/deals`, `/crm/activities`, `/crm/stages`, `/crm/proposals` e detalhes; fluxo completo lead → proposta aceita testado por HTTP; perfil somente leitura recebe `403` nas escritas.
- [x] API `/api/v1/crm` (21 rotas) com idempotência por `operation_key`; chave de outra organização = `409`; `docs/openapi.yaml` validado com `openapi-spec-validator`.
- [x] Teste de isolamento ampliado: organização B tenta 32 rotas do CRM (HTML e API) com IDs de A; nada vaza nem muda.
- [x] Simuladores identificados de e-mail e WhatsApp do CRM em `/integrations` (sem rede).
- [x] `fincore-seed-demo` inclui funil fictício; a parte do CRM é testada numa organização nova.
- [x] Servidor real iniciado contra o banco de teste e cinco telas do CRM conferidas no Firefox headless (screenshots `09` a `11`).
- [ ] Migrations `0019`/`0020` **não** foram aplicadas no banco de desenvolvimento (`127.0.0.1:5433`): rode `uv run tortoise -c fincore.config.database.TORTOISE_ORM migrate` (ou `docker compose up -d --build`) antes de abrir o CRM.
- [ ] `fincore-seed-demo` completo não foi reexecutado (o banco de teste já tem o usuário de demonstração e o comando recusa segunda execução); só a parte do CRM foi validada isoladamente.
- [ ] Imagem Docker não reconstruída nesta entrega.

Pendências conhecidas do CRM: envio real de e-mail/mensagens (sem provedor definido), funis múltiplos, automações, campos personalizados, importação de leads, responsável diferente do criador, arrastar e soltar no quadro, PDF da proposta e edição de proposta em rascunho.

Próximo passo sugerido: definir se o responsável (`owner`) de leads e negociações deve ser atribuível a outros membros e se o prazo de "negociação parada" deve ser configurável por organização.

## Revisão de segurança do CRM (2026-11-15)

Revisão manual do diff (isolamento por organização, CSRF, XSS, redirecionamentos, entradas hostis, concorrência) mais `bandit` e `pip-audit`.

- [x] Corrigido: `amount`/`estimated_value`/`discount` com dezenas de dígitos na API do CRM lançava `decimal.InvalidOperation` e respondia `500`; o limite agora é conferido antes do arredondamento (`400`).
- [x] Corrigido: `?message=` aceitava texto livre e o exibia como aviso de sucesso (content spoofing; o HTML já era escapado, não havia XSS). No CRM a URL leva só um código de `MESSAGES`; em `/contacts` (pré-existente) só mensagens de uma lista fechada.
- [x] Corrigido: duas propostas da mesma negociação podiam ser aceitas, gerando títulos a receber em dobro.
- [x] Corrigido (todas as rotas, pré-existente): caractere NUL em campo de formulário ou JSON chegava ao PostgreSQL e terminava em `500`; formulários descartam o NUL e a API responde `400`.
- [x] Testes de regressão para os quatro itens; suíte completa com 206 testes passando.
- [x] `pip-audit` sobre o `uv.lock`: nenhuma vulnerabilidade conhecida. `bandit`: só quatro avisos de baixa severidade em código anterior (senha fictícia do seed, texto da política de senha, chave de desenvolvimento já bloqueada fora do ambiente local, `xml.sax.saxutils.escape` usado para escapar texto no PDF).
- [x] Sem achados em: isolamento por organização (32 rotas do CRM testadas com IDs de outra organização), CSRF em todo POST, autoescape dos templates, redirecionamentos (só caminhos montados no servidor), SQL (ORM parametrizado; SQL das migrations só com constantes).

Riscos conhecidos, não alterados nesta entrega:

- Bootstrap e Font Awesome vêm de CDN sem Subresource Integrity (limitado pela CSP a `cdn.jsdelivr.net`/`cdnjs.cloudflare.com`).
- Não há limite de requisições por usuário fora do login.
- Listas de seleção de contatos nas telas do CRM não são paginadas.
- Abrir uma tela do CRM pela primeira vez grava as etapas padrão do funil, mesmo com perfil somente leitura.

## Regras financeiras e contábeis frente a ERPs (2026-11-15)

- [x] Estorno de movimentação espelha as contas do lançamento original e fica ligado a ele (testado: receita com categoria volta a zero; transferência estornada).
- [x] Baixa de título lança principal, juros/multa, desconto e tarifa em contas próprias (a receber e a pagar), com o razão do banco igual ao saldo financeiro.
- [x] Estorno de baixa: atômico, idempotente, mantém histórico, recalcula a situação do título; páginas e `POST /api/v1/titles/{id}/settlements/{settlement_id}/reverse`.
- [x] Bloqueios: estornar pelo extrato a movimentação de uma baixa; estornar um estorno; estornar no diário um lançamento gerado por movimentação.
- [x] Fechamento de período: recusa movimentação, baixa, efetivação de rascunho e estorno com data fechada sem deixar nada pela metade; só o proprietário altera (perfil financeiro recebe `403`).
- [x] Balancete, DRE e balanço patrimonial conferidos contra valores de referência; balanço fecha e o ativo bate com o saldo das contas financeiras.
- [x] Migrations `0021` (campos) e `0022` (`CHECK`); aplicadas no banco de teste; `makemigrations` sem diferenças.
- [x] Isolamento: organização B não estorna baixa de A (HTML e API).
- [x] Suíte completa: 221 testes passando; ruff, formatação e `openapi-spec-validator` limpos.
- [ ] Telas novas (demonstrativos, fechamento, lista de baixas) testadas por HTTP, mas não conferidas visualmente no navegador.
- [ ] Migrations `0021`/`0022` e o código novo não estão no container em execução; rode `docker compose -f compose.yaml -f compose.dev.yaml up -d --build`.

Diferenças em relação a ERPs que continuam, por decisão ou por escopo: regime de caixa (sem contas a receber/pagar no razão), sem impostos, sem multimoeda, sem encerramento de exercício, lançamento manual pode usar a conta contábil de um banco, baixas antigas continuam lançadas pelo valor líquido.

Próximo passo sugerido: decidir se o FinCore deve adotar regime de competência para títulos.
