# FinCore — ASP.NET Core MVC .NET 10

Guia para desenvolver uma aplicação financeira e contábil completa em português (pt-BR), com páginas renderizadas no servidor usando ASP.NET Core MVC, Razor Views, Bootstrap, HTML, CSS e JavaScript. Este arquivo descreve somente o projeto .NET.

## Stack

- Target framework `net10.0`; .NET 10 é LTS, com suporte previsto até novembro de 2028 ([política oficial de suporte](https://learn.microsoft.com/en-us/dotnet/core/releases-and-support)).
- ASP.NET Core MVC e Razor Views; sem SPA/framework front-end por padrão.
- PostgreSQL via Docker Compose.
- Entity Framework Core 10.x com provider Npgsql da mesma geração principal.
- ASP.NET Core Identity para contas, hash de senha e cookies de sessão.
- Bootstrap 5, CSS e JavaScript. Interface responsiva, pt-BR, paleta azul, tema claro/escuro, drawers e confirmação em modal.
- xUnit para testes unitários e de integração; PostgreSQL de teste isolado.
- Dinheiro em `decimal` e `numeric(15,2)` para BRL. Nunca usar `float` ou `double` para dinheiro.
- Segredos por User Secrets no desenvolvimento e ambiente/secret store no deploy.

## Criar solução

Pré-requisitos: SDK .NET 10 e Docker Compose v2. A partir da raiz do repositório:

```bash
mkdir -p dotnet/src dotnet/tests dotnet/docs
cd dotnet
dotnet new sln -n FinCore.DotNet
dotnet new mvc -f net10.0 -n FinCore.Web -o src/FinCore.Web
dotnet new classlib -f net10.0 -n FinCore.Domain -o src/FinCore.Domain
dotnet new classlib -f net10.0 -n FinCore.Application -o src/FinCore.Application
dotnet new classlib -f net10.0 -n FinCore.Infrastructure -o src/FinCore.Infrastructure
dotnet new xunit -f net10.0 -n FinCore.UnitTests -o tests/FinCore.UnitTests
dotnet new xunit -f net10.0 -n FinCore.IntegrationTests -o tests/FinCore.IntegrationTests
dotnet sln FinCore.DotNet.sln add src/FinCore.Web src/FinCore.Domain src/FinCore.Application src/FinCore.Infrastructure tests/FinCore.UnitTests tests/FinCore.IntegrationTests
dotnet add src/FinCore.Application reference src/FinCore.Domain
dotnet add src/FinCore.Infrastructure reference src/FinCore.Application src/FinCore.Domain
dotnet add src/FinCore.Web reference src/FinCore.Application src/FinCore.Infrastructure
dotnet add tests/FinCore.UnitTests reference src/FinCore.Domain src/FinCore.Application
dotnet add tests/FinCore.IntegrationTests reference src/FinCore.Web src/FinCore.Infrastructure
```

Referências oficiais: [tutorial ASP.NET Core MVC .NET 10](https://learn.microsoft.com/en-us/aspnet/core/tutorials/first-mvc-app/start-mvc?view=aspnetcore-10.0) e [migrations do EF Core](https://learn.microsoft.com/en-us/aspnet/core/data/ef-mvc/migrations?view=aspnetcore-10.0). Fixe versões compatíveis de EF Core e Npgsql em `Directory.Packages.props`; configure `dotnet-ef` como ferramenta local.

## Organização

```text
dotnet/
  FinCore.DotNet.sln
  Directory.Packages.props
  src/
    FinCore.Web/              controllers, Razor Views, ViewModels e assets
    FinCore.Application/      casos de uso, validações e interfaces
    FinCore.Domain/           entidades e regras sem dependência de web/EF
    FinCore.Infrastructure/   DbContext, Identity, migrations e adapters
  tests/
    FinCore.UnitTests/
    FinCore.IntegrationTests/
  docs/                       progresso, requisitos e decisões .NET
```

Controllers validam HTTP e delegam aos casos de uso; não implementam SQL nem regras contábeis. Views recebem ViewModels, não entidades ORM. Toda consulta deve aplicar a organização do usuário autenticado. Cada operação financeira local precisa ser atômica. Não mantenha transações abertas durante chamadas de rede.

## Módulos

- Identidade: cadastro, login/logout, sessão, proteção CSRF e escopo por organização.
- Dashboard: indicadores consultados do banco e separação entre realizado e previsto; sem dados fictícios.
- Contas financeiras: bancos, caixas e carteiras; saldos derivados.
- Categorias: grupos sintéticos e categorias analíticas de receita e despesa.
- Transações: receitas, despesas, transferências, liquidação, extrato e estorno.
- Títulos: contas a pagar e a receber independentes das transações; cadastro, filtros, parcelas, baixas parciais e cancelamento válido.
- Contabilidade: plano de contas, diário, partidas dobradas, razão, efetivação e estornos.
- Conciliação: importação OFX 1.x, matching, divergências, desmatch e auditoria.
- Relatórios: período, CSV e PDF.
- Integrações: simuladores locais para Pix, boleto, cartão, webhook e Open Finance.

Rotas MVC esperadas: `Account`, `Dashboard`, `FinancialAccounts`, `Categories`, `Transactions`, `Payables`, `Receivables`, `Accounting`, `Reconciliation`, `Reports` e `Integrations`. Ações POST validam antiforgery token e autorização. Paginação e filtros sempre têm limites server-side.

## Invariantes do domínio

### Movimento e título são conceitos diferentes

Uma transação representa movimento financeiro no extrato. Um título representa uma obrigação ou recebível. Criar título não altera caixa nem cria lançamento contábil. `TitleSettlement` registra a baixa e referencia a transação gerada atomicamente. O saldo aberto deriva do valor original menos valores aplicados.

Campos comuns do título: tipo pagar/receber, descrição, contato (fornecedor/cliente/outro), documento (número/tipo/série), categoria, centro de custo/projeto, valor original e moeda, emissão/vencimento, forma/condição de pagamento, conta, parcela/total, estado, observações e referência de anexo.

Baixa: valor aplicado, data, desconto, juros, multa, tarifas, valor líquido, conta, método e comprovante. Pagar também pode guardar beneficiário, código de barras/linha digitável, dados bancários/chave Pix e referência opcional de aprovador. Receber também pode guardar venda/pedido/contrato, meio e data de envio de cobrança, previsão de recebimento e link/identificador.

Parcelas são títulos próprios ligados por grupo e preservam o total decimal; centavos residuais vão para a última parcela. Baixa parcial atualiza saldo/estado e gera movimento idempotente. Vencido é calculado por data e saldo aberto. Título baixado não é cancelado nem apagado fisicamente. Anexos, comprovantes e cobrança são referências enquanto não houver armazenamento/provedor implementado.

### Contabilidade e consistência

- Lançamento efetivado tem ao menos duas linhas, débito igual ao crédito, valores não negativos e moeda consistente.
- Contas e categorias sintéticas agrupam; apenas as analíticas aceitam lançamentos/movimentos.
- Transferência gera movimentos pareados e não cria receita/despesa.
- Lançamento efetivado é imutável; correção usa estorno compensatório vinculado.
- Idempotência para importação OFX e operações financeiras repetíveis.
- Dinheiro sempre em `decimal`; estados de previsão, vencimento e liquidação não podem ser confundidos.

## Docker e PostgreSQL

Use um banco PostgreSQL dedicado ao .NET, por exemplo `fincore_dotnet`, e outro isolado para teste (`fincore_dotnet_test`). Configure credenciais locais em `.env`, mantenha volume persistente e healthcheck. Nunca use `EnsureDeleted`, `EnsureCreated`, `DROP DATABASE`, `TRUNCATE` ou remoção de volume no fluxo normal.

Use migrations versionadas do EF Core. Revise mudanças de schema e gere script idempotente para implantação:

```bash
dotnet ef migrations add NomeDaAlteracao --project src/FinCore.Infrastructure --startup-project src/FinCore.Web
dotnet ef migrations script --idempotent --project src/FinCore.Infrastructure --startup-project src/FinCore.Web
```

Não aplique migrations destrutivas em base com dados sem backup e estratégia expand/contract. Testes nunca apontam para banco de desenvolvimento ou produção.

## Segurança

- ASP.NET Core Identity para hashing, cookies HttpOnly/SameSite, expiração e logout.
- Escopo da organização derivado da sessão validada, nunca de um identificador arbitrário recebido do formulário.
- Antiforgery em POST, validação de ModelState, output encoding Razor e autorização em cada ação.
- Segredos fora do Git, logs sem tokens/senhas/dados bancários sensíveis.
- Rate limiting/proteção nas rotas de autenticação; HTTPS e cookies seguros em ambientes implantados.
- Nenhum pagamento real, acesso bancário ou consentimento Open Finance automático. Começar por mocks/sandbox.

## Testes e aceite

1. Unitários para dinheiro, parcelamento, estados, saldos, partidas dobradas, estorno e parser.
2. Integração PostgreSQL isolada para migrations, constraints, transações, idempotência, concorrência e isolamento por organização.
3. Testes HTTP para autenticação/CSRF, páginas, títulos/baixas, extrato, conciliação e exports.
4. Validar migrations em banco vazio e atualização com dados preservados; nunca limpar uma base compartilhada para conseguir teste verde.
5. Executar `dotnet restore`, `dotnet build`, `dotnet test` e análise estática.
6. Documentar setup, env vars, rotas, decisões e progresso em `readme_dotnet.md` e `dotnet/docs/`.

## Ordem de desenvolvimento

1. Fundação MVC, Docker/PostgreSQL, EF, migrations, `/health`, logging e CI.
2. Identity, organização, CSRF, autorização e isolamento demonstrado.
3. Contas, categorias, transações, transferências, extrato e saldos derivados.
4. Plano de contas, diário, partidas dobradas, liquidação e estornos.
5. Títulos, contatos, parcelas e baixas parciais vinculadas ao extrato.
6. OFX, conciliação, relatórios e dashboard real.
7. Simuladores de integração; providers reais exigem contrato, credenciais legítimas e aprovação específica.

Não declare módulo pronto com testes falhando ou persistência não verificada. Registre bloqueios externos e prossiga nas partes independentes.
