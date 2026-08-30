# Butter Relay — auditoria de segurança e qualidade (2026-08-29)

**Data:** 2026-08-29
**Escopo:** revisão de segurança/qualidade do checkout atual (`master`, commit `db84e01`), focada
em autenticação, exposição de dados relayed (payloads/tokens/PII), contrato de credencial de
relay com `asaas_payment`/`devbutter_backend`, e pontas soltas de implementação.
**Relação com `butter-relay-fluxos.md`:** aquele documento (2026-08-27) já contém um self-audit
estático cobrindo boa parte do que segue (tabela "Falhas e riscos priorizados", IDs H-01..H-04,
M-01..M-06, L-01). Este documento **não duplica** esses achados — apenas os referencia e liga a
prompts de correção acionáveis em `docs/prompts/`. Os itens abaixo marcados **[NOVO]** não
constavam em `butter-relay-fluxos.md`.

## Achados

### 1. API `/api/v1/*` sem autenticação — ref. H-01 (`butter-relay-fluxos.md`)

`app/projects/router.py`, `app/api_keys/router.py`, `app/templates_/router.py` e
`app/messages/router.py` só recebem `DbSession`; nenhuma rota usa `CurrentAdminUser` nem
dependência de API key. Qualquer chamador lê `payload`/destinatário/erro de todas as mensagens,
cria projetos, publica templates e gera/revoga chaves sem se autenticar.

Prompt: [`docs/prompts/autenticar-api-v1.md`](prompts/autenticar-api-v1.md).

### 2. [NOVO] `ApiKeyService.authenticate` é código morto

`app/api_keys/service.py:37-42` implementa validação de API key (hash + checagem de
`revoked_at` + `touch_last_used`), mas não é chamado em nenhum lugar do código -- não é dependência
FastAPI de nenhum router, nem é usado pelo worker (`app/worker/consumer.py:18-20` importa
`ApiKeyRepository` só com `# noqa: F401`, comentando que a fila usa IAM, não API key). Isto é,
o mecanismo de credencial de relay que o restante do monorepo pressupõe (ex.: fronteira de
"relay credential" citada em `asaas_payment`) existe em código mas nunca é exercitado -- reforça
o achado H-01/H-02: não é só falta de wiring, é uma funcionalidade pronta e nunca ligada.

Prompt: [`docs/prompts/ativar-api-key-service.md`](prompts/ativar-api-key-service.md).

### 3. Identidade do produtor não vinculada ao `project_id` no worker SQS — ref. H-02

`app/worker/consumer.py:51-60` aceita `MessageAttributes.project_id` da mensagem SQS como
autoridade, sem qualquer verificação de que quem publicou tem permissão para aquele projeto.
Em fila compartilhada, um produtor (`asaas_payment`, `devbutter_backend` ou qualquer serviço com
permissão de `SendMessage` na fila) pode se passar por outro projeto.

Prompt: [`docs/prompts/validar-project-id-worker-sqs.md`](prompts/validar-project-id-worker-sqs.md).

### 4. Preview de e-mail em iframe sem `sandbox` — ref. H-04

`app/admin/templates/partials/_preview.html:5-6` e
`app/admin/templates/message_detail.html:31-33` renderizam HTML de template/e-mail relayed via
`<iframe srcdoc="...">` sem atributo `sandbox`. Combinado com o achado 1 (publicação de template
sem autenticação), HTML/JS persistido pode executar no contexto do admin ao abrir o preview ou o
detalhe da mensagem.

Prompt: [`docs/prompts/sandbox-iframe-preview.md`](prompts/sandbox-iframe-preview.md).

### 5. `session_secret` inseguro por padrão e config que não falha fechado — ref. M-03

`app/settings/config.py:23` tem default `"dev-only-insecure-secret-change-me"`; `.env` e
`.env.example` mantêm `SESSION_SECRET=change-me-in-production` e `DEBUG=true`. Não há validação
de startup que rejeite esses valores fora de ambiente de desenvolvimento -- se a variável real
não for setada em produção, cookies de sessão ficam forjáveis.

Prompt: [`docs/prompts/session-secret-producao.md`](prompts/session-secret-producao.md).

### 6. Envio de mensagem não é idempotente — ref. H-03

`Message.sqs_message_id` (`app/messages/models.py`) tem apenas índice, não `unique`, e nunca é
consultado antes do `INSERT`. Redelivery do SQS cria uma `Message` nova a cada tentativa; se o
processo cair entre o `send` no Brevo e o `DeleteMessage`, o retry reenvia o e-mail.

Prompt: [`docs/prompts/idempotencia-mensagens-sqs.md`](prompts/idempotencia-mensagens-sqs.md).

### 7. Exposição de dados sensíveis em logs e respostas — ref. M-05

A API (hoje sem autenticação -- achado 1) devolve `payload`, destinatário e erro do provedor via
`MessageRead`. `app/core/email_provider.py:80-81` grava o `response.text` cru do Brevo em
`Message.last_error` e no log de exceção (via `@log_call`/`@log_errors` em
`app/core/decorators.py`), sem redação. Payloads relayed podem conter tokens/PII de
`asaas_payment`/`devbutter_backend`.

Prompt: [`docs/prompts/redacao-dados-sensiveis-logs.md`](prompts/redacao-dados-sensiveis-logs.md).

### 8. `Project.is_active` e `Project.sqs_queue_url` ignorados pelo worker — ref. M-06

`app/worker/consumer.py:103-116` usa somente `settings.sqs_queue_url` (global) e nunca lê
`project.is_active`. Um projeto desativado continua tendo mensagens processadas e enviadas.

Prompt: [`docs/prompts/respeitar-project-is-active-worker.md`](prompts/respeitar-project-is-active-worker.md).

### 9. [NOVO] Sem cobertura de teste para os módulos `messages` e `api_keys`

`tests/` contém `test_auth.py`, `test_decorators.py`, `test_projects.py`, `test_templates.py` e
`test_worker_consumer.py` -- não existe `test_messages.py` nem `test_api_keys.py`. Isso significa
que a view de detalhe de mensagem (`GET /admin/messages/{id}`, adicionada no commit `68cc684`),
o router `/api/v1/messages`, o router `/api/v1/api-keys` e o próprio `ApiKeyService` (achado 2)
não têm nenhum teste automatizado -- inclusive a falta de autenticação do achado 1 não é pega por
nenhum teste existente.

Prompt: [`docs/prompts/cobertura-testes-messages-api-keys.md`](prompts/cobertura-testes-messages-api-keys.md).

### 10. Classificação de erros do worker incompleta — ref. M-01/M-02

`run_forever` (`app/worker/consumer.py`) só captura `MalformedMessageError`, `NotFoundError` e
`EmailSendError`; erro de banco, `ValidationFailedError` de renderização ou falha do próprio
`delete_message` escapam do loop e deixam a mensagem em estado indefinido, sem distinguir
erro permanente de transitório (sem backoff/extensão de visibility timeout).

Prompt: [`docs/prompts/classificar-erros-worker.md`](prompts/classificar-erros-worker.md).

## Itens não convertidos em prompt (baixa prioridade / dependem de infraestrutura)

- L-01 (`butter-relay-fluxos.md`): limites de payload/paginação -- ligado à autenticação (achado
  1); revisar depois que a API tiver auth e escopo por tenant.
- M-04 (`butter-relay-fluxos.md`): rate limit/lockout de login e CSRF explícito nos POSTs de
  admin -- depende de decisão de produto (ex.: usar `slowapi`/proxy) e não tem um ponto único de
  código a corrigir; abrir prompt específico quando a decisão for tomada.

## Ordem sugerida de execução

1. `autenticar-api-v1.md`
2. `ativar-api-key-service.md`
3. `validar-project-id-worker-sqs.md`
4. `sandbox-iframe-preview.md`
5. `session-secret-producao.md`
6. `idempotencia-mensagens-sqs.md`
7. `redacao-dados-sensiveis-logs.md`
8. `respeitar-project-is-active-worker.md`
9. `cobertura-testes-messages-api-keys.md`
10. `classificar-erros-worker.md`
