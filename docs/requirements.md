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
