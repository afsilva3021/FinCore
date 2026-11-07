# FinCore

FinCore é uma aplicação web financeira e contábil completa em português (pt-BR). A interface usa páginas HTML renderizadas no servidor, Bootstrap, CSS e JavaScript. O backend atual é Python + Robyn + Tortoise ORM e PostgreSQL. A API JSON versionada complementa as páginas; não substitui a interface.

Este README serve como visão de produto e guia reproduzível para desenvolvedores e agentes de código. Leia também [AGENTS.md](AGENTS.md), que define invariantes e o fluxo obrigatório de implementação, e `docs/`, que contém contratos e decisões detalhados.

## Estado atual

- Aplicação local padrão: `http://127.0.0.1:8080`.
- PostgreSQL de desenvolvimento: `127.0.0.1:5433`, banco `fincore`.
- PostgreSQL de teste: `127.0.0.1:5434`, banco `fincore_test`.
- Verificação: `GET /health` responde `{"status":"ok","database":"connected"}` quando o banco está acessível.
- Migrations versionadas até `0017`; migrations são aditivas e preservam registros. Depois de atualizar o repositório, rode `migrate` antes de subir a aplicação (veja [Migrations](#migrations-e-preservação-dos-dados)).
- Não há dados de demonstração na visão geral. Os dados exibidos vêm da organização autenticada.
- Pix, boletos, cartões, webhooks e Open Finance são somente simuladores locais. Não enviam pagamentos nem acessam provedores.
- A integração CNAB não possui parser ativo. Existe apenas um contrato para conectar adapters de banco, padrão e versão de layout explicitamente escolhidos.
- O modelo de títulos é separado das transações. O cadastro de título não altera o extrato ou saldos; sua baixa cria uma movimentação vinculada.

## Atualizações recentes

Registro resumido do que mudou; o detalhe validado fica em [docs/progress.md](docs/progress.md). Toda implementação ou melhoria deve acrescentar uma linha aqui.

| Data | Mudança |
|---|---|
| 2026-11-06 | Arquitetura: `web.py` (2.600 linhas) dividido em `presentation/routes/` por área; decorators `login_required`/`api_login_required` no lugar de 43 blocos repetidos; todas as consultas das rotas movidas para `application/queries.py` (inclusive o N+1 restante da conciliação); testes de arquitetura. |
| 2026-11-06 | Camada `domain/` com as regras financeiras puras (parcelas, baixas, estados de título, partidas dobradas), testadas sem banco; arredondamento contábil padronizado em meio para cima. |
| 2026-11-06 | Integridade e desempenho: 17 `CHECK` constraints no banco (migration `0017`), saldos contábeis e financeiros agregados no banco (fim dos N+1), paginação de 50 itens nas listas (antes havia um corte silencioso em 100/200) e teste de isolamento entre organizações em todas as rotas com ID. |
| 2026-11-06 | Perfis de acesso (Proprietário, Financeiro, Somente leitura) com página `/members`; bloqueio central de escrita para quem é somente leitura. CI passa a construir a imagem Docker, subir o Compose e rodar os testes com o usuário restrito do banco. |
| 2026-11-06 | Segurança de login: bloqueio de 15 min após 5 falhas por e-mail ou 20 por IP, tempo de resposta igual para e-mail inexistente, logout que revoga a sessão em todos os dispositivos e headers de segurança (CSP sem `unsafe-inline`, `X-Frame-Options`, `nosniff`, HSTS com HTTPS). Migration `0016`. |
| 2026-11-06 | Aplicação em container próprio, separado do banco: Dockerfile sem root, banco só em rede interna, usuário de banco restrito (sem DDL) para o app, migrations automáticas e bloqueio de `AUTH_SECRET` padrão fora do ambiente local. |
| 2026-11-06 | Ícones de mostrar/ocultar senha também no cadastro; senhas dos testes de integração ajustadas à política de senha. |
| 2026-11-05 | Contatos (clientes/fornecedores) com CPF/CNPJ, endereço via CEP, inativação; dados bancários nas contas financeiras (migrations `0014` e `0015`). |
| 2026-11-05 | Cadastro com confirmação de senha e política de força validada no servidor. |
| 2026-11-05 | Valores pt-BR: `1.500` passa a ser lido como R$ 1.500,00 (antes, R$ 1,50). |
| 2026-11-05 | Token CSRF dura o mesmo que a sessão; formulários não falham mais em silêncio após 1 hora. |
| 2026-11-05 | Totais de receitas e despesas abatem estornos e são somados no banco. |
| 2026-11-05 | Baixa e cancelamento de título mostram o motivo real da recusa. |

## Stack e arquitetura atuais

| Camada | Tecnologia / localização |
|---|---|
| Runtime e dependências | Python 3.14, `uv`, `pyproject.toml`, `uv.lock` |
| HTTP e páginas | Robyn, rotas em `src/fincore/presentation/`, templates Jinja em `templates/` |
| Interface | Bootstrap 5, HTML, CSS em `static/css/`, JavaScript em `static/js/` |
| Aplicação | Casos de uso em `src/fincore/application/` |
| Persistência | PostgreSQL 16, Tortoise ORM e migrations em `src/fincore/infrastructure/` |
| Execução | Docker Compose com containers separados para aplicação, migrations e banco; `uv run fincore` para desenvolvimento |
| Qualidade | pytest, httpx, Ruff |

É um **monólito modular em camadas**, com casos de uso no estilo *Transaction Script* e persistência *Active Record* via Tortoise ORM. As dependências seguem uma direção só:

```text
presentation/routes  →  application (casos de uso + queries)  →  domain (regras puras)
                                     ↓
                              infrastructure (models Tortoise, migrations, ViaCEP)
```

- **`domain/`** guarda as regras financeiras puras (dinheiro, parcelas, baixas, estados de título, partidas dobradas) e não importa Robyn, Tortoise nem outras camadas, então é testado em milissegundos sem banco.
- **`application/`** busca os dados, aplica as regras do domínio dentro de transações e grava; `application/queries.py` concentra as leituras usadas pelas telas.
- **`presentation/`** só traduz HTTP: lê formulários/JSON, chama a aplicação e renderiza. Rotas não consultam o ORM diretamente.
- `tests/unit/test_architecture.py` verifica essas duas regras no código-fonte e falha se alguém as quebrar.

Por que não Clean Architecture completa: com um ORM Active Record, separar entidades de domínio dos models exigiria duplicar cada entidade e um mapeador entre elas. A escolha foi isolar o que mais ganha com isso — as regras financeiras — e manter o resto simples. O arquivo de inicialização é `src/fincore/app.py`; não existe `app.py` na raiz. A aplicação web usa sessão assinada e cookies; formulários de alteração validam CSRF. Senhas são protegidas com Argon2id. Consultas financeiras são limitadas à organização da sessão.

## Funcionalidades presentes

### Identidade e organização

- Cadastro de usuário e organização, login e logout.
- Hash Argon2id, sessão assinada (12 h), cookies HttpOnly/SameSite e CSRF com a mesma duração da sessão, renovado no login/cadastro.
- Login com bloqueio temporário: 5 falhas para o mesmo e-mail ou 20 para o mesmo IP em 15 minutos bloqueiam novas tentativas por 15 minutos (resposta `429`). O bloqueio vale também para e-mails inexistentes e a senha é sempre verificada contra um hash, então a resposta não revela quais contas existem. Atrás de proxy, configure `TRUSTED_PROXIES` para o IP real do cliente ser usado.
- Logout revoga a sessão: o token carrega uma versão (`session_version`) que é incrementada no logout, invalidando cookies copiados em qualquer dispositivo.
- Headers de segurança em todas as respostas da aplicação: CSP sem scripts inline, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `Permissions-Policy` e, com `SECURE_COOKIES=true`, HSTS. Arquivos de `/static` são servidos direto pelo Robyn sem esses headers; em produção, o proxy reverso deve aplicá-los a tudo.
- Cadastro exige confirmação de senha e senha de 12 a 128 caracteres com pelo menos 3 tipos (minúscula, maiúscula, número, símbolo), sem conter o nome ou o início do e-mail. A regra fica no servidor (`application/identity.py`); a página apenas a espelha para retorno imediato.
- Isolamento de contas, categorias, movimentações, títulos, conciliações e relatórios por organização.
- Perfis de acesso por organização, gerenciados em `/members` pelo proprietário:

  | Perfil | Pode |
  |---|---|
  | Proprietário | Tudo, inclusive adicionar membros, mudar perfis e desativar acessos |
  | Financeiro | Cadastrar e alterar dados financeiros e contábeis; não gerencia membros |
  | Somente leitura | Consultar telas e relatórios; nenhuma alteração |

  A permissão de escrita é verificada num middleware antes de todas as rotas (`POST`/`PUT`/`PATCH`/`DELETE`), então uma rota nova já nasce protegida; o bloqueio responde `403` em HTML ou JSON. A organização sempre mantém pelo menos um proprietário ativo. Membros desativados não entram, mas o histórico é preservado.
- Cadastro próprio de clientes e fornecedores em `/customers` e `/suppliers`, com CPF/CNPJ, endereço, contato, inativação e reativação.
- Consulta de endereço por CEP via ViaCEP, acionada pelo formulário de contato; os campos também podem ser preenchidos manualmente.

### Contas, categorias e transações

- Cadastro de contas financeiras bancárias, caixa e carteira, com banco, código bancário, agência com dígito e número da conta; saldos derivados dos movimentos realizados.
- Categorias de receita e despesa hierárquicas: grupos sintéticos e categorias analíticas; movimentos usam apenas categorias analíticas.
- Receitas, despesas, transferências, movimentos em aberto, liquidação, extrato interno, idempotência e estornos compensatórios.
- Uma transferência cria movimentos pareados de origem/destino, sem gerar receita ou despesa.
- Valores monetários usam `Decimal` no backend e `NUMERIC(15,2)` no banco; nunca usar `float` para dinheiro.
- O banco também aplica as regras principais com `CHECK` constraints (migration `0017`): valores positivos, débito **ou** crédito em cada linha contábil, ajustes de baixa não negativos e tipos/situações válidos. Se um banco antigo tiver linhas fora da regra, a migration não falha: cria a constraint para gravações novas e avisa (`NOTICE`) para corrigir os dados e rodar `VALIDATE CONSTRAINT`.
- Transações, títulos, contatos, diário contábil e conciliação são paginados em 50 itens (`?page=N`), preservando os filtros.
- Formulários aceitam valores em pt-BR (`1.500`, `1.234,56`, `R$ 1.500,00`): ponto entre grupos de 3 dígitos é milhar e vírgula é decimal; formatos ambíguos (`1,000.50`) são recusados. A API JSON usa a notação decimal canônica (`"250.00"`). Regras em `domain/money.py`.

**Transações e títulos não são o mesmo cadastro.** Uma transação representa um movimento financeiro que aparece no extrato; um título representa uma obrigação a pagar ou um recebível. Não transformar títulos em tipos, filtros ou aliases de transações.

### Contas a pagar e a receber (títulos)

- Títulos independentes, contatos vinculados, busca, filtros por situação e vencimento e estado vazio.
- Os contatos cadastrados podem ser reutilizados na criação de títulos; contatos novos ainda podem ser informados diretamente no formulário.
- Cadastro por drawer responsivo com descrição, contato, documento (número, tipo e série), categoria, centro de custo/projeto, valor, datas de emissão/vencimento, forma e condição, conta, parcelas, observações e referência de anexo.
- Campos específicos a pagar: beneficiário, código de barras/linha digitável, dados bancários ou chave Pix e referência opcional de aprovador.
- Campos específicos a receber: pedido/contrato/venda, meio e data de envio da cobrança, previsão de recebimento e link/identificador.
- Parcelas são títulos próprios, ligadas por grupo, e preservam o total com valores decimais exatos.
- Baixas parciais/finais guardam valor aplicado, data, desconto, juros, multa, tarifas, valor líquido, conta, forma e referência do comprovante. O saldo é derivado dos valores aplicados.
- Baixa e transação do extrato são gravadas atomicamente; títulos sem baixa não alteram saldo. Cancelamento é permitido somente para título aberto sem baixa. Títulos com baixa não são excluídos fisicamente. Quando uma baixa ou cancelamento é recusado, a página do tipo do título (a pagar/receber) mostra o motivo.
- Anexos, cobrança, comprovante, aprovação e dados bancários são referências informadas pelo usuário. Não há upload de arquivos, processo de aprovação nem conexão de pagamento implementados.

### Contabilidade

- Plano de contas hierárquico sintético/analítico, contas por natureza (ativo, passivo, patrimônio líquido, receita e despesa), diário, débitos/créditos, partidas dobradas, livro razão e estornos.
- Lançamentos efetivados precisam ter no mínimo duas linhas e total de débitos igual ao total de créditos. Grupos sintéticos não recebem partidas.
- Movimentações liquidadas e transferências geram lançamentos contábeis vinculados atomicamente.
- Estorno gera lançamento compensatório, preservando o original. Há comando idempotente de backfill para movimentos liquidados históricos.
- Regras completas: [docs/accounting.md](docs/accounting.md).

### Conciliação e relatórios

- Importação de OFX 1.x SGML para uma conta selecionada, deduplicação por FITID, revisão manual, conciliar/desconciliar e auditoria.
- Match exige conta, data e valor correspondentes; divergências são rejeitadas.
- Relatórios por período, exportação CSV e PDF.
- Totais de receitas e despesas (visão geral e relatórios) consideram só movimentos liquidados e abatem estornos da natureza do movimento original; o estorno entra no período da sua própria data. Transferências não entram nesses totais.
- Limites OFX: não processa OFX 2.x XML, múltiplas contas no mesmo arquivo, conversão de moeda ou upload multipart.

### API e demonstrações

- API JSON `/api/v1`: saúde, sessão/CSRF, contas, categorias e transações (listar, consultar, criar, liquidar e estornar), com paginação e isolamento por organização.
- Títulos são operados pelas páginas HTML `/payables` e `/receivables`; ainda não há API JSON de títulos.
- `/integrations` permite executar simuladores locais de Pix, boleto, cartão, webhook e Open Finance.
- Contratos e rotas: [docs/api.md](docs/api.md). Limites de integrações: [docs/external-integrations.md](docs/external-integrations.md).

## Modelo de dados e regras entre módulos

Os modelos Tortoise estão em `src/fincore/infrastructure/models.py`; migrations em `src/fincore/infrastructure/migrations/` são a fonte versionada do schema.

| Modelo | Responsabilidade e vínculos principais |
|---|---|
| `Organization`, `User`, `Membership` | Identidade, organização e associação que limita o tenant de cada sessão |
| `FinancialAccount` | Conta financeira BRL, tipo, banco e metadados; saldo é calculado dos movimentos liquidados e saldo inicial |
| `Category` | Receita/despesa, tipo sintético/analítico, categoria superior e vínculo contábil |
| `FinancialTransaction` | Movimento do extrato, valor decimal, competência/vencimento, status, conta/categoria, ator da liquidação, idempotência, transferência/estorno |
| `Counterparty` | Contato de título (fornecedor, cliente ou outro) pertencente à organização |
| `FinancialTitle` | Título a pagar/receber, contato, conta/categoria, documento, valores/datas, parcela, estado e referências específicas |
| `TitleSettlement` | Baixa de título com valor aplicado, ajustes, valor líquido, comprovante e referência única à transação gerada |
| `LedgerAccount` | Plano contábil, natureza e hierarquia sintética/analítica |
| `JournalEntry`, `JournalEntryLine` | Cabeçalho e linhas de débito/crédito; efetivação e estorno rastreáveis |
| `ImportedStatementTransaction` | Linha importada de OFX e identidade externa por conta/FITID |
| `ReconciliationMatch`, `ReconciliationEvent` | Vínculo com transação interna e trilha de conciliar/desconciliar |

As regras contábeis, estados e invariantes estão detalhados em [docs/accounting.md](docs/accounting.md), [docs/requirements.md](docs/requirements.md) e [AGENTS.md](AGENTS.md). Não criar um campo de saldo editável como fonte da verdade; derive valores realizados dos movimentos/diário. Datas de competência, vencimento e liquidação representam conceitos diferentes.

## Instalação reproduzível

### Pré-requisitos

- Git.
- Docker Engine/Desktop com Docker Compose v2.
- Python 3.14 e `uv` (ou instale `uv` pelo método oficial da sua plataforma).
- Porta `8080` livre. A porta `5433` só é usada no modo de desenvolvimento (`compose.dev.yaml`); o banco de teste usa `5434`.

### Opção A — tudo em containers (aplicação separada do banco)

É o modo recomendado e o mesmo desenho que será usado em produção. A aplicação e o banco rodam em **containers separados**:

```text
            host (127.0.0.1:8080)
                    │
          ┌─────────▼─────────┐   rede frontend (saída para internet, ex.: ViaCEP)
          │   app (FinCore)   │
          └─────────┬─────────┘
                    │ rede backend (internal: sem internet, sem porta no host)
   ┌────────────────┼────────────────┐
   │ migrate (roda e termina)        │ db (PostgreSQL, volume pgdata)
   └─────────────────────────────────┘
```

- **O banco não é alcançável de fora:** fica só na rede interna `backend`, sem porta publicada no host.
- **Dois usuários de banco:** o `migrate` usa o dono (`POSTGRES_USER`) para aplicar migrations e depois cria/atualiza o usuário restrito `APP_DB_USER`. O `app` conecta só com esse usuário, que pode ler e gravar dados, mas **não** cria, altera nem apaga tabelas e não acessa o histórico de migrations.
- **Container da aplicação endurecido:** usuário sem root (`uid 10001`), sistema de arquivos somente leitura, `cap_drop: ALL`, `no-new-privileges` e healthcheck em `/health`.
- **Segredos obrigatórios:** o Compose não sobe sem `POSTGRES_PASSWORD`, `APP_DB_PASSWORD` e `AUTH_SECRET`; a aplicação recusa subir exposta na rede com o `AUTH_SECRET` padrão ou com menos de 32 caracteres.

```bash
cp .env.example .env
# edite o .env: troque POSTGRES_PASSWORD, APP_DB_PASSWORD e AUTH_SECRET
python -c "import secrets; print(secrets.token_urlsafe(48))"   # gera um AUTH_SECRET
docker compose up -d --build
docker compose ps            # migrate deve aparecer como "exited (0)"; app e db "healthy"
```

Abra `http://127.0.0.1:8080`. As migrations rodam sozinhas a cada `up`, então depois de um `git pull` basta repetir `docker compose up -d --build`.

Operar só a aplicação, sem tocar no banco:

```bash
docker compose up -d --build app     # reconstrói e reinicia só o app (roda migrate antes)
docker compose logs -f app
docker compose restart app
docker compose stop app
```

`docker compose down` para **todos** os serviços (o volume é mantido). Para mexer só na aplicação, sempre informe `app` no comando.

### Opção B — aplicação fora do Docker (desenvolvimento)

Útil para depurar com recarga rápida. O arquivo `compose.dev.yaml` publica o banco em `127.0.0.1:5433` só para isso:

```bash
uv sync
cp .env.example .env
docker compose -f compose.yaml -f compose.dev.yaml up -d db
uv run tortoise -c fincore.config.database.TORTOISE_ORM migrate
uv run fincore
```

Nesse modo a aplicação conecta como o usuário dono e escuta só em `127.0.0.1`, por isso aceita o `AUTH_SECRET` padrão. Não use `compose.dev.yaml` em produção.

### Uso e cuidados com os dados

Crie o usuário e a organização pela tela de cadastro. As senhas do `.env.example` são públicas e servem só para uso local; não envie `.env` ao Git.

O PostgreSQL usa o volume persistente `pgdata`. **Não execute `docker compose down -v`, `docker volume rm`, `TRUNCATE`, limpeza de migrations ou comandos de reset em bancos com dados.** Alterações de schema devem usar migrations aditivas e reversíveis sempre que possível.

Para verificar o serviço:

```bash
curl http://127.0.0.1:8080/health
```

Para movimentos contábeis históricos anteriores ao vínculo com o razão, o backfill opcional é idempotente:

```bash
uv run fincore-backfill-accounting
# ou, com containers:
docker compose run --rm app fincore-backfill-accounting
```

### Variáveis de ambiente

`.env.example` é a configuração local executável. Variáveis principais:

| Variável | Padrão local | Uso |
|---|---|---|
| `APP_HOST` | `127.0.0.1` | Interface de rede do Robyn (no container: `0.0.0.0`) |
| `APP_PORT` | `8080` | Porta HTTP |
| `POSTGRES_USER` | `fincore` | Usuário dono do banco; no Docker, usado só pelo `migrate` |
| `POSTGRES_PASSWORD` | `change-me-locally` | Senha local apenas |
| `POSTGRES_DB` | `fincore` | Banco de desenvolvimento |
| `POSTGRES_HOST` | `127.0.0.1` | Host PostgreSQL visto pela aplicação |
| `POSTGRES_PORT` | `5433` | Porta publicada para desenvolvimento |
| `APP_DB_USER` | `fincore_app` | Usuário restrito (sem DDL) usado pelo container `app` |
| `APP_DB_PASSWORD` | `change-me-app-locally` | Senha do usuário restrito (16+ caracteres) |
| `APP_BIND` / `APP_PUBLISHED_PORT` | `127.0.0.1` / `8080` | Onde o Compose publica a aplicação no host |
| `DATABASE_URL` | ausente | Se definida, substitui os campos PostgreSQL |
| `AUTH_SECRET` | chave de desenvolvimento | Assinatura de sessão; obrigatório trocar (32+ caracteres) quando a aplicação escuta fora de `127.0.0.1` |
| `SECURE_COOKIES` | `false` | Ativar `true` atrás de HTTPS (também liga o HSTS) |
| `TRUSTED_PROXIES` | vazio | IPs de proxies reversos confiáveis, separados por vírgula; só deles o `X-Forwarded-For` é aceito para o limite de login por IP |

O Compose de teste tem credenciais fixas exclusivamente locais em `compose.yaml`, porta 5434 e volume `pgtestdata`. Nunca reutilize esses valores em produção.

### Antes de ir para produção

O Compose já separa aplicação e banco e usa padrões seguros, mas produção exige mais alguns passos:

- [ ] **HTTPS na frente da aplicação:** um proxy reverso (Caddy, Nginx, Traefik ou o balanceador do provedor) termina o TLS e encaminha para o container `app`. Não exponha a porta 8080 diretamente na internet.
- [ ] `SECURE_COOKIES=true` (cookies só trafegam por HTTPS).
- [ ] Segredos fortes e únicos para `POSTGRES_PASSWORD`, `APP_DB_PASSWORD` e `AUTH_SECRET`, guardados num cofre de segredos (não em arquivo versionado). Trocar o `AUTH_SECRET` derruba todas as sessões ativas.
- [ ] Backup automatizado e testado do volume/banco (`pg_dump` agendado ou backup do serviço gerenciado) antes de qualquer migration.
- [ ] Se usar banco gerenciado (RDS, Cloud SQL etc.), remova o serviço `db` e aponte `POSTGRES_HOST` para ele; mantenha os dois usuários (dono para migrations, restrito para o app) e exija TLS na conexão.
- [ ] Pendências de segurança da aplicação listadas em [docs/progress.md](docs/progress.md): checagem de perfil (`Membership.role`). Configure `TRUSTED_PROXIES` com o IP do proxy reverso.

### Banco de teste e testes

O banco de teste é separado e persistente. Os testes não apagam dados e podem deixar registros de teste nele:

```bash
docker compose --profile test up -d db-test
DATABASE_URL=postgres://fincore_test:fincore_test_local@127.0.0.1:5434/fincore_test uv run tortoise -c fincore.config.database.TORTOISE_ORM migrate
DATABASE_URL=postgres://fincore_test:fincore_test_local@127.0.0.1:5434/fincore_test uv run pytest
```

Testes sem `DATABASE_URL` ignoram casos que exigem persistência PostgreSQL. Nunca configure os testes para apontar ao banco de desenvolvimento ou produção. Com o banco de teste já migrado, execute `uv run pytest` para a suíte unitária e de integração.

Checagens de qualidade:

```bash
uv run ruff check .
uv run ruff format --check .
node --check static/js/app.js
```

## Migrations e preservação dos dados

- A sequência versionada fica em `src/fincore/infrastructure/migrations/`.
- Para gerar uma migration depois de alterar modelos, inspecione o diff e rode `uv run tortoise -c fincore.config.database.TORTOISE_ORM makemigrations`.
- Aplique com `uv run tortoise -c fincore.config.database.TORTOISE_ORM migrate`. Faça isso sempre depois de `git pull`: subir a aplicação com migrations pendentes causa erros como `column ... does not exist`.
- Para conferir se os models e as migrations estão alinhados, `makemigrations` deve responder `No changes detected`.
- Em teste, defina `DATABASE_URL` para o serviço de teste antes dos comandos.
- Antes de uma mudança, leia as migrations existentes, preserve registros e inclua teste de compatibilidade. Não use `generate_schemas()` como migration de produção.

## Estrutura do repositório

```text
src/fincore/
  app.py                         inicialização Robyn, health e lifecycle ORM
  domain/                        regras puras (sem banco/framework): dinheiro, partidas dobradas, títulos
  application/                   casos de uso: buscam dados, aplicam as regras do domain e gravam
  config/                        variáveis de ambiente e conexão ORM
  infrastructure/                 modelos Tortoise e migrations versionadas
  infrastructure/db_roles.py     usuário restrito do banco usado pelo container app
  application/queries.py         consultas de leitura usadas pelas telas e pela API
  presentation/web.py            sessão, CSRF, templates, paginação e decorators de login
  presentation/routes/           rotas HTTP, um arquivo por área (finance, accounting, api...)
templates/                        templates Jinja/Bootstrap por módulo
static/css/                       tema claro/escuro e estilos de interface
static/js/                        drawer, confirmações modais e interações
Dockerfile                        imagem da aplicação (sem root, só leitura)
compose.yaml                      app, migrate, db (rede interna) e db-test
compose.dev.yaml                  só dev: publica o banco em 127.0.0.1:5433
docs/                             requisitos, contratos, contabilidade e decisões
tests/unit/                       regras de domínio sem banco e testes de arquitetura
tests/integration/                persistência PostgreSQL e fluxos HTTP
```

## Documentos de referência

- `AGENTS.md`: regras de negócio, segurança, arquitetura e instruções para implementação autônoma.
- `docs/requirements.md`: histórias de usuário e critérios de aceite.
- `docs/progress.md`: estado validado, pendências e próximo passo.
- `docs/decisions.md`: decisões técnicas e limitações conhecidas.
- `docs/api.md`: rotas HTML e contrato JSON.
- `docs/accounting.md`: regras de partidas dobradas.
- `docs/external-integrations.md`: como planejar integrações futuras.
