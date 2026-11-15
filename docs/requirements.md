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

## CRM

- Como vendedor, cadastro um lead com origem e valor estimado, registro os critérios de qualificação e o converto em contato e negociação sem duplicar contatos.
- Como vendedor, acompanho negociações por etapa do funil, movo de etapa, marco ganho ou perda (com motivo) e vejo o histórico de quem alterou.
- Como vendedor, registro tarefas, ligações, reuniões, e-mails e notas ligadas a um lead, negociação ou contato e concluo as pendentes.
- Como vendedor, monto uma proposta com itens, desconto e validade; ao registrar o aceite, o sistema gera os títulos a receber parcelados para o financeiro.
- Como gestor, vejo conversão de leads, taxa de ganho, previsão ponderada, ciclo médio e negociações paradas.

Critérios de aceite:

- Conversão de lead é atômica, única e recusa contato duplicado por nome ou e-mail.
- Negociação encerrada não muda de etapa; perda exige motivo; toda mudança registra usuário e horário.
- Aceite de proposta gera títulos que somam exatamente o total, não movimenta caixa, e reenvios (inclusive simultâneos) não duplicam títulos.
- Proposta não enviada, vencida ou recusada não pode ser aceita.
- Indicadores batem com valores de referência calculados no teste e não misturam organizações.
- Perfil somente leitura consulta, mas não altera; dados de outra organização não são lidos nem alterados por ID.
- O CRM não envia e-mail nem mensagens; qualquer envio é simulação identificada.

## Regras contábeis no padrão de ERP

- Como financeiro, estorno uma baixa lançada por engano e o título volta a ficar em aberto, com caixa e razão compensados.
- Como contador, vejo juros, multas, descontos e tarifas em contas próprias, separados da receita ou despesa principal.
- Como proprietário, fecho um período para que ninguém lance, baixe ou estorne com data retroativa.
- Como contador, consulto balancete de verificação, DRE e balanço patrimonial por período.

Critérios de aceite:

- Estorno de movimentação zera exatamente as contas do lançamento original.
- Estorno de baixa é atômico e idempotente; a baixa fica no histórico; a movimentação de uma baixa não é estornada pelo extrato.
- Período fechado recusa todo lançamento com data até o fechamento, sem deixar operação pela metade; só o proprietário fecha e reabre.
- No balanço, Ativo = Passivo + Patrimônio líquido + Resultado acumulado; o ativo das contas financeiras é igual ao saldo derivado das transações.
