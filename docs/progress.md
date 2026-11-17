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

## Correções de revisão (2026-11-05)

- [x] Valores em pt-BR: ponto separando grupos de 3 dígitos é milhar (`1.500` = R$ 1.500,00); formatos ambíguos são rejeitados. Parsers de formulário centralizados em `application/money.py`; a API mantém a notação decimal canônica (`"250.00"`).
- [x] Token CSRF dura o mesmo que a sessão (12 h) e é rotacionado no login/cadastro; antes, formulários falhavam em silêncio depois de 1 h.
- [x] Totais de receitas e despesas (visão geral e relatórios) abatem estornos da natureza do movimento original e são agregados no banco.
- [x] Baixa e cancelamento de título exibem o motivo real da recusa na página do tipo correto.
- [x] Cadastro exige confirmação de senha e aplica política de força no servidor (com retorno imediato no navegador); campos preenchidos são preservados após erro, exceto as senhas.
- [x] Ruff e suíte completa (74 testes, com PostgreSQL de teste) aprovados.
- [x] Pendências da revisão concluídas em 2026-11-06 (segredo obrigatório, limite de login, revogação de sessão, headers, perfis, N+1 e `CHECK` constraints).

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

## ERP fase 8 — cadastros de produto (2026-11-17)

Primeira fase do roteiro em [erp-roadmap.md](erp-roadmap.md). Não há estoque, compras nem vendas ainda.

- [x] Modelos `ProductGroup`, `UnitOfMeasure`, `Product` e `Warehouse`; migration `0019` aditiva, aplicada em banco limpo (0001→0019) e no banco de teste com 44 organizações existentes (todas receberam 5 unidades e 1 depósito padrão). `makemigrations` responde `No changes detected`.
- [x] Regras puras em `domain/products.py` (SKU, GTIN com dígito verificador, NCM, quantidades sem arredondamento silencioso, casas decimais por unidade), testadas sem banco.
- [x] Banco reforça as regras: `CHECK` de preço/estoque mínimo/formatos, SKU único por organização, código de barras único por organização quando informado e exatamente um depósito padrão por organização (índices únicos parciais).
- [x] Concorrência: cinco cadastros simultâneos com o mesmo SKU gravam um só; trocas simultâneas de depósito padrão terminam com exatamente um padrão.
- [x] Páginas `/products` (busca, filtros, paginação, drawer de cadastro/edição, inativar) e `/product-settings` (grupos, unidades e depósitos); API `GET/POST /api/v1/products` e `GET /api/v1/products/{id}`; `docs/openapi.yaml` validado com `openapi-spec-validator`.
- [x] Perfil `stock` (Estoque) e permissão de escrita por área: `stock` altera só cadastros de produto, `finance` só o financeiro, `owner` tudo, `viewer` nada. Testado nas páginas e na API.
- [x] Isolamento entre organizações: 15 novas tentativas (HTML e API) no teste de isolamento, incluindo criar produto apontando para unidade ou grupo de outra organização.
- [x] `fincore-seed-demo` cria grupos, um segundo depósito e seis produtos fictícios.
- [x] Ruff e suíte completa aprovados: 181 testes (122 unitários, 59 de integração com PostgreSQL 16 local na porta 5434).
- [x] Telas abertas no Chrome com os dados do seed (lista, drawer e cadastros auxiliares). Bootstrap foi servido de uma cópia local porque o ambiente não alcança a CDN.
- [x] Marcadores de conflito de merge que estavam neste arquivo foram removidos, preservando as duas seções.
- [ ] Não validado neste ambiente: `docker compose up --build` (sem Docker) e o job `docker` do CI.
- [x] Trocar a unidade de um produto com movimentos de estoque passou a ser recusado na fase 9.
- [ ] `infrastructure/models.py` continua em arquivo único; a divisão por área proposta no roteiro fica para depois, porque o teste de arquitetura lê esse arquivo.


## Demonstração online (2026-11-17)

- [x] `render.yaml`: aplicação pelo `Dockerfile` e PostgreSQL 16 no plano gratuito do Render, com `AUTH_SECRET` gerado, `SECURE_COOKIES=true` e seed de demonstração na primeira subida.
- [x] Comando de início validado localmente com URL `postgresql://` em banco limpo: primeira subida aplica 0001→0019, cria o seed e responde `/health`; segunda subida mantém os dados e sobe normalmente.
- [ ] Não validado: a publicação no Render (exige a conta do responsável). O build da imagem também não foi executado neste ambiente.

## ERP fase 9 — estoque (2026-11-17)

Segunda fase do roteiro em [erp-roadmap.md](erp-roadmap.md). Compras e vendas ainda não existem; as movimentações são manuais.

- [x] Modelos `StockMovement` (imutável) e `StockBalance` (cache da posição); migration `0020` aditiva, aplicada em banco limpo (0001→0020) e no banco de teste já populado. `makemigrations` responde `No changes detected`.
- [x] Custo médio ponderado móvel em `domain/inventory.py`: a posição guarda quantidade e valor total, saídas usam o custo médio vigente e a saída que zera a quantidade leva todo o valor, então não sobra nem falta centavo. Testado sem banco.
- [x] Entrada (saldo inicial ou ajuste), saída (ajuste), transferência entre depósitos e estorno em `application/inventory.py`, cada um numa única transação com a posição bloqueada (`SELECT ... FOR UPDATE`).
- [x] Estoque negativo bloqueado: 12 saídas simultâneas de 1 unidade com saldo 5 gravam exatamente 5; transferências simultâneas em sentidos opostos não travam (posições bloqueadas sempre na mesma ordem).
- [x] Idempotência por `operation_key`: reenvio e 6 envios simultâneos com a mesma chave gravam um movimento só; chave de outra organização é conflito.
- [x] Estorno por movimento oposto que restaura quantidade e valor exatos; só o último movimento da posição pode ser estornado, e a transferência é desfeita nas duas pernas ou em nenhuma.
- [x] Contabilidade: saldo inicial (D Estoque / C Patrimônio de abertura), ajuste de entrada (D Estoque / C Ajustes positivos) e ajuste de saída (D Perdas / C Estoque) geram lançamento efetivado e balanceado; o estorno gera o lançamento inverso ligado ao original. O saldo da conta `SYS-STOCK` bate com o valor das posições. Regras em `docs/accounting.md`.
- [x] Banco reforça as regras: `CHECK` de quantidades e valores não negativos e de sentidos/motivos, sequência única por posição e gatilho que recusa `UPDATE`/`DELETE` em `stockmovement`.
- [x] Teste de consistência: em cada cenário, a posição guardada é comparada com a soma dos movimentos (quantidade e valor) e com a fotografia do último movimento.
- [x] Páginas `/stock` (posição, valor em estoque, abaixo do mínimo, formulário de movimentação) e `/stock/movements` (histórico com estorno); coluna "Em estoque" em `/products`. API `/api/v1/stock/balances` e `/api/v1/stock/movements` (listar, movimentar, estornar); `docs/openapi.yaml` validado.
- [x] Produto com movimentos não troca de unidade; produto com saldo não desliga o controle de estoque; depósito com saldo não é inativado.
- [x] Permissão: área de escrita `inventory` para `/stock` e `/api/v1/stock`; `owner` e `stock` movimentam, `finance` e `viewer` só consultam.
- [x] Isolamento entre organizações: 5 novas tentativas de escrita e 5 listagens no teste de isolamento.
- [x] Correção encontrada no navegador: o servidor Robyn entrega os parâmetros da URL sem decodificar, então mensagens de confirmação apareciam como `Contato+cadastrado.` e buscas com acento ou espaço não encontravam nada (os testes não pegavam porque o cliente de teste já entrega o valor decodificado). Todas as rotas passaram a ler a URL por `_query()` em `presentation/web.py`.
- [x] Ruff e suíte completa aprovados: 227 testes (151 unitários, 76 de integração com PostgreSQL 16 local), também com o usuário restrito do banco.
- [x] Fluxo real no Chrome com os dados do seed: transferência pelo formulário, saída recusada por falta de saldo com o formulário preservado, estorno com confirmação e campos que mudam conforme o tipo.
- [ ] Não validado neste ambiente: `docker compose up --build` (sem Docker) e o job `docker` do CI.
- [ ] O histórico é ordenado pela ordem de registro; a data informada é só informativa e não reordena o custo médio.
- [ ] O formulário de movimentação lista todos os produtos num `select`; com muitos produtos vai precisar de busca.

Próximo passo: fase 10 (compras) — pedido de compra, recebimento com entrada em estoque e contas a pagar por competência.
