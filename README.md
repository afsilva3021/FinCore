# FinCore

FinCore é uma aplicação web financeira e contábil completa em português (pt-BR). A interface usa páginas HTML renderizadas no servidor, Bootstrap, CSS e JavaScript. O backend atual é Python + Robyn + Tortoise ORM e PostgreSQL. A API JSON versionada complementa as páginas; não substitui a interface.

Este README serve como visão de produto e guia reproduzível para desenvolvedores e agentes de código. Leia também [AGENTS.md](AGENTS.md), que define invariantes e o fluxo obrigatório de implementação, e `docs/`, que contém contratos e decisões detalhados.

## Estado atual

- Aplicação local padrão: `http://127.0.0.1:8080`.
- PostgreSQL de desenvolvimento: `127.0.0.1:5433`, banco `fincore`.
- PostgreSQL de teste: `127.0.0.1:5434`, banco `fincore_test`.
- Verificação: `GET /health` responde `{"status":"ok","database":"connected"}` quando o banco está acessível.
- Migrations versionadas até `0013`; migrations são aditivas e preservam registros.
- Não há dados de demonstração na visão geral. Os dados exibidos vêm da organização autenticada.
- Pix, boletos, cartões, webhooks e Open Finance são somente simuladores locais. Não enviam pagamentos nem acessam provedores.
- A integração CNAB não possui parser ativo. Existe apenas um contrato para conectar adapters de banco, padrão e versão de layout explicitamente escolhidos.
- O modelo de títulos é separado das transações. O cadastro de título não altera o extrato ou saldos; sua baixa cria uma movimentação vinculada.

## Stack e arquitetura atuais

| Camada | Tecnologia / localização |
|---|---|
| Runtime e dependências | Python 3.14, `uv`, `pyproject.toml`, `uv.lock` |
| HTTP e páginas | Robyn, rotas em `src/fincore/presentation/`, templates Jinja em `templates/` |
| Interface | Bootstrap 5, HTML, CSS em `static/css/`, JavaScript em `static/js/` |
| Aplicação | Casos de uso em `src/fincore/application/` |
| Persistência | PostgreSQL 16, Tortoise ORM e migrations em `src/fincore/infrastructure/` |
| Execução local | Docker Compose para banco; comando `uv run fincore` para aplicação |
| Qualidade | pytest, httpx, Ruff |

É um monólito modular, com apresentação, aplicação, domínio e infraestrutura separados por responsabilidade. O arquivo de inicialização é `src/fincore/app.py`; não existe `app.py` na raiz. A aplicação web usa sessão assinada e cookies; formulários de alteração validam CSRF. Senhas são protegidas com Argon2id. Consultas financeiras são limitadas à organização da sessão.

## Funcionalidades presentes

### Identidade e organização

- Cadastro de usuário e organização, login e logout.
- Hash Argon2id, sessão assinada, cookies HttpOnly/SameSite e CSRF.
- Isolamento de contas, categorias, movimentações, títulos, conciliações e relatórios por organização.

### Contas, categorias e transações

- Cadastro de contas financeiras bancárias, caixa e carteira, com saldos derivados dos movimentos realizados.
- Categorias de receita e despesa hierárquicas: grupos sintéticos e categorias analíticas; movimentos usam apenas categorias analíticas.
- Receitas, despesas, transferências, movimentos em aberto, liquidação, extrato interno, idempotência e estornos compensatórios.
- Uma transferência cria movimentos pareados de origem/destino, sem gerar receita ou despesa.
- Valores monetários usam `Decimal` no backend e `NUMERIC(15,2)` no banco; nunca usar `float` para dinheiro.

**Transações e títulos não são o mesmo cadastro.** Uma transação representa um movimento financeiro que aparece no extrato; um título representa uma obrigação a pagar ou um recebível. Não transformar títulos em tipos, filtros ou aliases de transações.

### Contas a pagar e a receber (títulos)

- Títulos independentes, contatos vinculados, busca, filtros por situação e vencimento e estado vazio.
- Cadastro por drawer responsivo com descrição, contato, documento (número, tipo e série), categoria, centro de custo/projeto, valor, datas de emissão/vencimento, forma e condição, conta, parcelas, observações e referência de anexo.
- Campos específicos a pagar: beneficiário, código de barras/linha digitável, dados bancários ou chave Pix e referência opcional de aprovador.
- Campos específicos a receber: pedido/contrato/venda, meio e data de envio da cobrança, previsão de recebimento e link/identificador.
- Parcelas são títulos próprios, ligadas por grupo, e preservam o total com valores decimais exatos.
- Baixas parciais/finais guardam valor aplicado, data, desconto, juros, multa, tarifas, valor líquido, conta, forma e referência do comprovante. O saldo é derivado dos valores aplicados.
- Baixa e transação do extrato são gravadas atomicamente; títulos sem baixa não alteram saldo. Cancelamento é permitido somente para título aberto sem baixa. Títulos com baixa não são excluídos fisicamente.
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
- Portas `5433` e `8080` livres. O banco de teste usa `5434`.

### Primeira inicialização

Na raiz do repositório:

```bash
uv sync
cp .env.example .env
docker compose up -d db
uv run tortoise -c fincore.config.database.TORTOISE_ORM migrate
uv run fincore
```

Abra `http://127.0.0.1:8080`, crie o usuário e a organização pela tela de cadastro e use o sistema. O servidor permanece no terminal; `Ctrl+C` o encerra. A senha local de desenvolvimento presente no `.env.example` é pública e insegura para produção. Troque-a para qualquer ambiente compartilhado ou implantado; não envie `.env` ao Git.

O serviço PostgreSQL usa volume persistente `pgdata`. `docker compose down` para os serviços sem remover o volume. **Não execute `docker compose down -v`, `docker volume rm`, `TRUNCATE`, limpeza de migrations ou comandos de reset em bancos com dados.** Alterações de schema devem usar migrations aditivas e reversíveis sempre que possível.

Para verificar o serviço:

```bash
curl http://127.0.0.1:8080/health
```

Para movimentos contábeis históricos anteriores ao vínculo com o razão, o backfill opcional é idempotente:

```bash
uv run fincore-backfill-accounting
```

### Variáveis de ambiente

`.env.example` é a configuração local executável. Variáveis principais:

| Variável | Padrão local | Uso |
|---|---|---|
| `APP_HOST` | `127.0.0.1` | Interface de rede do Robyn |
| `APP_PORT` | `8080` | Porta HTTP |
| `POSTGRES_USER` | `fincore` | Usuário do banco |
| `POSTGRES_PASSWORD` | `change-me-locally` | Senha local apenas |
| `POSTGRES_DB` | `fincore` | Banco de desenvolvimento |
| `POSTGRES_HOST` | `127.0.0.1` | Host PostgreSQL visto pela aplicação |
| `POSTGRES_PORT` | `5433` | Porta publicada para desenvolvimento |
| `DATABASE_URL` | ausente | Se definida, substitui os campos PostgreSQL |
| `AUTH_SECRET` | chave de desenvolvimento | Assinatura de sessão; substituir fora de ambiente local descartável |
| `SECURE_COOKIES` | `false` | Ativar `true` atrás de HTTPS |

O Compose de teste tem credenciais fixas exclusivamente locais em `compose.yaml`, porta 5434 e volume `pgtestdata`. Nunca reutilize esses valores em produção.

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
- Aplique com `uv run tortoise -c fincore.config.database.TORTOISE_ORM migrate`.
- Em teste, defina `DATABASE_URL` para o serviço de teste antes dos comandos.
- Antes de uma mudança, leia as migrations existentes, preserve registros e inclua teste de compatibilidade. Não use `generate_schemas()` como migration de produção.

## Estrutura do repositório

```text
src/fincore/
  app.py                         inicialização Robyn, health e lifecycle ORM
  application/                   casos de uso financeiro, contábil, títulos e OFX
  config/                        variáveis de ambiente e conexão ORM
  infrastructure/                 modelos Tortoise e migrations versionadas
  presentation/                   health e rotas server-rendered/API
templates/                        templates Jinja/Bootstrap por módulo
static/css/                       tema claro/escuro e estilos de interface
static/js/                        drawer, confirmações modais e interações
docs/                             requisitos, contratos, contabilidade e decisões
tests/unit/                       regras isoladas
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
