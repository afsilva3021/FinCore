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

## CRM (2026-11-15)

- **Contato do CRM é o `Counterparty` existente:** negociações e propostas usam o mesmo cadastro de clientes/fornecedores dos títulos, sem tabela nova de contas e sem hierarquia empresa/pessoa. Vincular um lead a um contato que era só fornecedor o marca também como cliente. Reversível: uma hierarquia pode ser acrescentada depois por migration aditiva.
- **Lead é entidade separada do contato:** lead não qualificado não polui o cadastro usado no financeiro; só entra como contato na conversão.
- **Ganho/perda são situações da negociação, não etapas do funil** (modelo de Pipedrive/RD Station), o que mantém a previsão ponderada só sobre negociações abertas.
- **Etapas padrão criadas no primeiro uso do CRM** (`ensure_default_stages`, idempotente), em vez de migration de dados ou de acoplar o cadastro de organização ao CRM. Efeito colateral aceito: a primeira consulta de uma tela do CRM, mesmo por perfil somente leitura, grava as quatro etapas padrão.
- **Qualificação:** para marcar um lead como qualificado exige-se necessidade confirmada e ao menos mais um dos critérios (orçamento, decisor, prazo). É uma escolha do FinCore; ajustável em `MIN_QUALIFICATION_SCORE`.
- **Duplicidade na conversão:** mesmo e-mail ou mesmo nome (sem diferenciar maiúsculas) em contato da organização bloqueia a criação de contato novo; o usuário vincula ao existente. Não há fusão automática.
- **Proposta aceita gera título, não transação:** respeita a regra de que título não movimenta caixa. O aceite chama `create_titles` com o contato da negociação (novo parâmetro opcional `counterparty_id`, aditivo) dentro da mesma transação que marca proposta e negociação. Não se cria lançamento contábil no aceite, igual a qualquer título cadastrado.
- **Total da proposta é derivado** (itens − desconto); o total de cada linha é gravado na criação e a proposta não é editada depois, só descartada.
- **Numeração de proposta sequencial por organização**, com bloqueio da linha da organização na transação e unicidade `(organization, number)` no banco.
- **"Enviar proposta" é só registro:** não há provedor de e-mail/mensagens definido. E-mail e WhatsApp existem apenas como simuladores identificados em `/integrations`, sem rede.
- **Negociação parada** = 14 dias sem mudar de etapa (`STALLED_AFTER_DAYS`), fixo por enquanto.
- **Quadro sem arrastar e soltar:** mover etapa é um formulário com seletor, o que funciona sem JavaScript e com leitor de tela; limite de 300 negociações abertas no quadro.
- **Pesquisa de referência:** as regras vieram de resumos de busca e comparativos sobre EspoCRM, SuiteCRM, Twenty, Odoo, ERPNext, Zoho, RD Station e Pipedrive; a documentação oficial desses produtos não foi lida por inteiro. Nenhum código foi copiado.

## Regras financeiras e contábeis frente a ERPs (2026-11-15)

Comparação com o comportamento conhecido de ERPNext, Odoo e Django Ledger (referências do `AGENTS.md`), feita de memória do funcionamento desses produtos, sem consultar a documentação nesta entrega.

- **Regime de caixa mantido.** ERPs lançam a fatura por competência (contas a receber/pagar). Mudar exigiria lançar todo título no razão e migrar os existentes; fica como decisão de produto pendente. Consequência registrada em `docs/accounting.md`.
- **Baixa com contas separadas** para juros/multa, descontos e tarifas, como nos ERPs, em vez de jogar o valor líquido na conta da categoria. Só vale para baixas novas.
- **Estorno espelha o lançamento original**, em vez de usar uma conta genérica.
- **Estorno de baixa** como operação própria que reabre o título; estornar a movimentação da baixa pelo extrato passou a ser recusado (antes deixava o título "pago" com o dinheiro devolvido).
- **Título com baixas apenas estornadas pode ser cancelado.** Lê-se a regra "título com baixa não é cancelado" como baixa em vigor; a baixa estornada permanece no histórico.
- **Lançamento gerado por movimentação não é estornado no diário**, e estorno não é estornado.
- **Trava de período** por data única na organização (como o lock date do Odoo), alterável só pelo proprietário. Não há períodos nomeados nem encerramento de exercício.
- **Liquidação com competência em período fechado** lança na data da liquidação; alternativa (recusar) travaria movimentos antigos em aberto sem saída.
- **Demonstrativos derivados do razão**, com resultado acumulado calculado no balanço em vez de lançamento de encerramento.
