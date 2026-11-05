# Guia para adicionar uma integração externa

Este guia será usado quando houver provedor, contrato e credenciais legítimas definidos. Até lá, as telas de Pix, boletos, cartões, webhooks e Open Finance continuam em simulação local. Nenhum procedimento deste documento autoriza pagamentos ou consentimentos reais por padrão.

## 1. Defina o contrato antes do adapter

Registre em uma decisão ou documento específico:

- provedor, produto, versão da API e ambiente (sandbox ou produção);
- endpoints oficiais, autenticação, limites, timeouts e códigos de erro;
- operações permitidas e seus efeitos financeiros;
- política de idempotência, consulta de status e reconciliação;
- formato e validação de assinatura de webhooks, janela contra replay e retries;
- requisitos legais, segurança e aprovação operacional aplicáveis.

Use documentação oficial do provedor. Se algum item estiver indefinido, mantenha o adapter simulado.

## 2. Preserve as fronteiras do FinCore

Implemente o contrato como protocolo em `src/fincore/application/` e o cliente concreto em `src/fincore/infrastructure/`. Casos de uso recebem uma interface, não importam SDK ou detalhes HTTP do provedor. Rotas Robyn validam entrada e chamam o caso de uso; templates exibem estado real ou claramente simulado.

Não faça chamadas externas dentro de transações PostgreSQL longas. Registre uma intenção local com chave de idempotência; faça a chamada fora da transação; grave o resultado numa transação curta. Se houver entrega assíncrona, use outbox durável e retries limitados, sem presumir transação distribuída.

## 3. Configure segredos

- Adicione somente nomes de variáveis e valores fictícios a `.env.example`.
- Leia credenciais por `fincore.config.settings`; não as coloque em templates, logs, fixtures, comandos ou respostas HTTP.
- Separe sandbox de produção por configuração explícita e segura. Produção deve ser opt-in e permanecer desabilitada por padrão.
- Mantenha `.env` fora do Git; rotacione credenciais expostas e não reutilize credenciais de produção em testes.

## 4. Implemente proteções obrigatórias

- Chave idempotente persistida e resposta consistente para retries.
- Timeout explícito, limites de payload, validação server-side e tratamento de indisponibilidade.
- Webhooks: autenticação criptográfica conforme contrato, comparação em tempo constante, janela contra replay, idempotência e armazenamento mínimo do evento necessário.
- Nunca registre tokens, dados bancários completos, senhas ou conteúdo sensível do webhook.
- Operações com dinheiro devem passar por fluxo de aprovação definido; testes e desenvolvimento usam sandbox/mock e nunca enviam pagamentos reais.

## 5. Teste antes de habilitar

1. Unit tests do adapter com HTTP mockado, cenários válidos, timeout, erro e retry.
2. Testes de contrato com fixtures oficiais anonimizadas e versionadas, respeitando os termos do provedor.
3. Testes de persistência para idempotência, concorrência, trilha de auditoria e isolamento por organização.
4. Testes de webhook para assinatura inválida, replay e evento duplicado.
5. Validação HTTP da UI e do adapter em sandbox isolado.
6. Atualize `docs/api.md`, `docs/decisions.md`, `.env.example`, migrations quando necessárias e `docs/progress.md`.

Produção só pode ser habilitada depois de provedor, credenciais, requisitos operacionais e validação de segurança estarem definidos. Não declare conformidade regulatória sem avaliação específica.
