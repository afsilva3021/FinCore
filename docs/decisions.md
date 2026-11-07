# Decisões técnicas

## Fundação inicial

- **Python 3.14:** versão estável instalada no ambiente e suportada pela versão atual do uv; fixada em `.python-version` e restringida no `pyproject.toml`.
- **Tortoise migrations:** usar o CLI nativo documentado pelo Tortoise atual (`tortoise init`, `makemigrations`, `migrate`) em vez de Aerich, que a documentação classifica como alternativa legada. Confirmar os comandos na versão resolvida antes de registrar migrations.
- **PostgreSQL local:** o banco fica só na rede interna `backend` do Compose, sem porta no host; `compose.dev.yaml` publica `127.0.0.1:5433` apenas para desenvolvimento fora do Docker. Variáveis de usuário, senha e database alimentam tanto o serviço quanto o healthcheck.
- **URL do banco:** `DATABASE_URL` pode substituir a composição local padrão. O arquivo `.env.example` é apenas um exemplo, sem credencial real.
- **Saúde:** `GET /health` confirma conectividade do banco e devolve 503 em indisponibilidade, sem expor exceções ao cliente.
- **Escopo da fundação:** a fase inicial não implementava operações financeiras ou alegações de integração com bancos; módulos funcionais foram adicionados nas fases seguintes.
- **Interface:** aplicação server-rendered em Robyn com templates Jinja, Bootstrap 5, CSS e JavaScript próprios. A API apoia a interface e futuras integrações; o painel web é a experiência principal.
- **Identidade:** Argon2id para senhas; sessão assinada com validade de 12 horas em cookie HttpOnly/SameSite=Lax; token CSRF assinado em cookie acessível ao formulário; organizações isoladas por associação de usuário.
- **Auditoria de criador:** novos cadastros de contas/categorias guardam o usuário criador; a FK fica anulável para preservar registros antigos e permitir coexistência com versões que ainda não escrevem esse campo. Novos casos de uso sempre informam o usuário autenticado.
- **Auditoria de liquidação:** transações existentes podem ter `settled_by` nulo; ações novas exigem usuário autenticado e registram quem liquidou junto com o horário UTC.
- **Assets Bootstrap:** Bootstrap e fonte DM Sans são carregados de CDN. O restante do estilo e comportamento da aplicação fica em `static/`.
- **OFX:** primeira versão aceita OFX 1.x SGML colado no formulário, ligado manualmente a uma conta BRL. A extensão não presume conversão de moeda, múltiplas contas ou OFX 2.x.
- **PDF:** ReportLab foi adicionado para exportação local de relatório sem serviço externo. CSV usa biblioteca padrão.
- **Integrações:** Pix, boletos, cartões, webhooks e Open Finance retornam somente cenários fictícios gerados localmente. Sem rede, pagamento, consentimento ou token real.
- **CNAB pendente:** parser definitivo permanece bloqueado até definir banco e versão/layout 240 ou 400; não se anuncia compatibilidade universal.
- **Integrações externas:** `docs/external-integrations.md` descreve o fluxo obrigatório para escolher provedor, configurar credenciais, implementar adapter e validar sandbox antes de qualquer ativação real.
- **Títulos versus transações:** `FinancialTitle` é cadastro independente para obrigações e recebíveis; não é alias nem tipo de `FinancialTransaction`. `TitleSettlement` guarda a baixa e referencia uma transação efetiva criada na mesma transação PostgreSQL. O saldo do título é derivado dos valores aplicados, enquanto ajustes ficam registrados na baixa. A migration inicial dos títulos é aditiva e não converte movimentos antigos em títulos, pois isso inventaria dados de fornecedor/cliente/documento.
- **Visão geral financeira:** os indicadores e listas são calculados a partir das contas e movimentações persistidas para a organização autenticada. Quando não há registros, a interface mostra estados vazios; não há dados de demonstração na página.
- **Cadastro de clientes/fornecedores:** contatos usam a entidade `Counterparty` já vinculada a títulos, ampliada por migration aditiva. Cliente e fornecedor são papéis independentes (um contato pode ter ambos); não se duplica entidade nem se apaga fisicamente ao inativar. CPF/CNPJ é normalizado e único por organização quando informado.
- **Consulta de CEP:** usa ViaCEP como serviço público opcional, com timeout e tratamento de CEP inválido/indisponibilidade. A consulta só preenche o formulário e não impede edição manual; não há credencial nem dependência de cadastro nos Correios.
- **Interface consistente:** cadastros com formulário extenso abrem drawer lateral pelo botão de ação; o JavaScript compartilhado administra múltiplos drawers sem adicionar framework de frontend.

- **Aplicação separada do banco (2026-11-06):** a aplicação roda em container próprio (`Dockerfile`, usuário `uid 10001`, sistema de arquivos somente leitura, `cap_drop: ALL`). O serviço `migrate` aplica migrations com o usuário dono e executa `fincore-setup-app-db-role`, que cria/atualiza de forma idempotente o usuário `APP_DB_USER` com apenas `SELECT/INSERT/UPDATE/DELETE` (sem DDL, sem acesso a `tortoise_migrations`). Validado rodando a suíte completa conectada com esse usuário. Limitação: a imagem não foi construída no ambiente da implementação (registries de imagem bloqueados); `docker compose config` e o fluxo equivalente fora de container foram validados.
- **Bloqueio de segredo padrão:** `fincore` recusa iniciar quando `APP_HOST` não é local e `AUTH_SECRET` é o padrão ou tem menos de 32 caracteres.
- **CHECK constraints com NOT VALID + VALIDATE (2026-11-06):** a migration `0017` não derruba bancos antigos com dados fora da regra; a constraint passa a valer para gravações novas e um `NOTICE` indica o que validar depois.
- **Paginação por página (offset) nas telas HTML:** simples e suficiente para o volume esperado; a API mantém `limit`/`offset`. Cursor por data pode substituir se as tabelas crescerem muito.
