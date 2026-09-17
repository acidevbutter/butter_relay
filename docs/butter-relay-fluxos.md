# Butter Relay — fluxos, controles e lacunas

**Data:** 2026-08-27  
**Escopo:** checkout atual de `butter_relay`; análise estática de código, configuração, testes e documentação.  
**Status:** não é prova de execução integrada, AWS, Postgres, SQS, DLQ, IAM, Secrets Manager ou Brevo.

> **Nota de atualização (2026-09-16):** o transporte migrou de SQS (`app/worker/consumer.py`) para
> Celery/Redis (`app/worker/celery_app.py`, `app/worker/tasks.py`) — `devbutter_backend` já
> publicava nesse contrato e o worker deste repo não o implementava, causando perda silenciosa de
> mensagens (`acidevbutter/butter_relay#10`). F4 e as referências a `SQS_QUEUE_URL`/`ReceiveMessage`/
> `DeleteMessage` abaixo descrevem o transporte antigo; o mecanismo (long-poll → ack) e o formato
> (`{template_key, to, payload}` + identificador de tenant) permanecem equivalentes via
> `send_task("butter_relay.process_message", kwargs={project_id, template_key, to, payload})`.
> H-03 (envio não idempotente) continua valendo, agora sobre o id da task Celery em vez do
> `MessageId` do SQS. M-06, como descrita, já não procede: o consumidor sempre verificou
> `project.is_active` (linha então em `app/worker/consumer.py:97-100`) — a parte de
> `Project.sqs_queue_url` ser ignorada continua verdadeira e agora é permanente (campo legado, sem
> substituto por-projeto). Ver `README.md` para a documentação viva do fluxo atual.
>
> **Nota de atualização (2026-09-17):** M-06 agora está **resolvido**, não mais "permanente":
> `Project.sqs_queue_url` foi removido (`acidevbutter/butter_relay#16`) e cada projeto passou a ter
> sua própria fila Celery derivada do `slug` (`Project.queue_name`, `relay-<slug>`), consumida
> automaticamente pelo worker via `app/worker/queues.py` (sem restart, via `add_consumer` +
> seed em `worker_ready`). `relay-shared` permanece como fila padrão/legada para produtores ainda
> não migrados. Também foi adicionado `Project.external` + `ApiKey.expires_at` (30 dias para
> projetos externos) — ver `README.md`.

## Visão atual

Diagramas:

- [`architecture.mmd`](diagrams/architecture.mmd): componentes e limites de confiança.
- [`message-flow-sequence.mmd`](diagrams/message-flow-sequence.mmd): processamento real de uma mensagem e estados de falha.

O serviço possui três superfícies:

| Fluxo | Entrada | Comportamento observado | Saída |
|---|---|---|---|
| Administração | `/admin/*` | Login por formulário, sessão assinada e operações globais de projetos, chaves, templates e mensagens | HTML/Jinja2; envio manual direto ao Brevo |
| API JSON | `/api/v1/*` | CRUD/listagem de projetos, templates, chaves e mensagens | JSON; atualmente sem dependência de autenticação |
| Worker | SQS configurada em `SQS_QUEUE_URL` | Lê `project_id` do atributo, carrega projeto/template, renderiza, grava mensagem, envia ao Brevo e apaga a mensagem em sucesso | E-mail e registro em `messages` |

## Fluxos existentes

### F1 — Login e administração

1. `POST /admin/login` valida email e senha com bcrypt.
2. A sessão recebe apenas `admin_user_id`; a cada rota administrativa o usuário é recarregado e `is_active` é conferido.
3. Projetos, API keys, templates e mensagens são consultados ou alterados pelo dashboard.
4. `POST /admin/messages/{id}/resend` renderiza e envia diretamente pelo processo da API.
5. `POST /admin/logout` limpa a sessão.

Evidência: `app/auth/router.py:22-62`, `app/auth/service.py:11-33`, `app/admin/router.py:55-339`.

### F2 — API JSON de configuração e consulta

As rotas em `app/projects/router.py`, `app/api_keys/router.py`, `app/templates_/router.py` e `app/messages/router.py` recebem somente `DbSession` e parâmetros do pedido. Não há `CurrentAdminUser`, API-key dependency, escopo por tenant ou outra guarda de autorização.

Isso permite, no estado atual, criar/alterar projetos, publicar templates, gerar/revogar chaves e ler mensagens sem autenticação. A API também devolve `payload`, destinatário, erro e `provider_message_id` nas mensagens.

### F3 — Publicação e renderização de template

1. O serviço busca a última versão, desativa a ativa e cria a próxima versão.
2. O payload opcional é validado contra `variables_schema`.
3. Subject e HTML são renderizados por `SandboxedEnvironment(autoescape=True)`.
4. O preview JSON e o preview HTMX retornam o resultado renderizado.

Evidência: `app/templates_/service.py:10-70`, `app/templates_/router.py:23-56`, `app/admin/router.py:218-236`.

### F4 — Consumo SQS e envio

1. O worker faz long polling na única URL `settings.sqs_queue_url`.
2. O atributo SQS `project_id` e o corpo JSON (`template_key`, `to`, `payload`) são parseados.
3. Projeto e template ativo são carregados.
4. É criada uma linha `Message` em `pending`; depois ela passa a `processing` e incrementa `attempts`.
5. O worker chama Brevo por HTTPS.
6. Em sucesso, grava `sent` e `provider_message_id`, e então chama `DeleteMessage`.
7. Em `EmailSendError`, grava `failed` e deixa a mensagem SQS sem apagar para nova entrega.

Evidência: `app/worker/consumer.py:38-131`, `app/messages/repository.py:32-59`, `app/core/email_provider.py:48-84`.

## Pontos fortes

- Senhas administrativas usam bcrypt; não há rota pública de cadastro; usuário inativo não autentica.
- API keys são aleatórias, armazenadas apenas como SHA-256, exibidas em claro somente na criação e podem ser revogadas.
- Templates usam sandbox Jinja2, autoescape e validação JSON Schema.
- Versionamento de templates e unicidade de `(project_id, key, version)` são persistidos no banco.
- Foreign keys, índices, tratamento de conflitos e rollback estão presentes nos repositórios.
- O worker usa `asyncio.to_thread` para chamadas boto3 bloqueantes e mantém o fluxo de entrega separado do processo HTTP.
- A abstração `EmailProvider`, HTTPS fixo para Brevo, request ID e logs de erro fornecem pontos de extensão e diagnóstico.
- Falhas do provedor não apagam a mensagem SQS, permitindo retry/redrive quando a infraestrutura estiver configurada.

## Falhas e riscos priorizados

| ID | Severidade | Constatação e impacto |
|---|---|---|
| H-01 | Alta | **API JSON sem autenticação/autorização.** Os routers `/api/v1/*` não exigem sessão nem chave. Um chamador remoto pode ler dados de todos os projetos/mensagens, alterar configuração, publicar HTML e gerar chaves. Quebra autenticação, autorização e isolamento multi-tenant. |
| H-02 | Alta | **Identidade do produtor não está vinculada ao `project_id`.** Em uma fila compartilhada, o `project_id` é um atributo controlado pelo produtor e é aceito como autoridade pelo worker. A API key criada não é usada no consumo (`app/worker/consumer.py:18-20,51-60`). Um produtor com permissão de envio na fila pode se passar por outro projeto e usar seu template/remetente. |
| H-03 | Alta | **Envio não é idempotente.** `sqs_message_id` tem somente índice, não unicidade nem lookup; cada redelivery cria nova `Message`. Se o e-mail for aceito e o processo cair antes de `DeleteMessage`, o retry envia novamente. Falhas e retries também fragmentam `attempts` em várias linhas. |
| H-04 | Alta | **Preview administrativo permite HTML ativo em iframe sem sandbox.** Templates são conteúdo editável e o preview usa `srcdoc` sem atributo `sandbox` em `app/admin/templates/partials/_preview.html:5-6` e `app/admin/templates/message_detail.html:31-33`. Com a publicação não autenticada atual, HTML/JavaScript persistido pode atingir o contexto do dashboard quando um administrador abre o preview. |
| M-01 | Média | **Erros fora de três classes derrubam ou interrompem o worker.** `run_forever` captura apenas `MalformedMessageError`, `NotFoundError` e `EmailSendError`. Falha de banco, `ValidationFailedError` de renderização, erro inesperado ou falha de `DeleteMessage` escapa do loop. Mensagens podem ficar sem estado final e ser reprocessadas sem classificação. |
| M-02 | Média | **Retries não distinguem erro permanente de transitório.** 401 do Brevo, template ausente e payload inválido seguem o mesmo caminho de deixar a mensagem invisível expirar. Não há backoff, extensão de visibility timeout, limite definido no código ou confirmação local de DLQ/redrive. |
| M-03 | Média | **Segredos e configuração insegura não falham fechado.** `session_secret` possui placeholder inseguro por padrão (`app/settings/config.py:21-24`), `SQS_QUEUE_URL` e `BREVO_API_KEY` aceitam vazio, e `.env.example` usa `DEBUG=true`. Não há validação de startup que impeça produção com configuração de desenvolvimento. |
| M-04 | Média | **Proteções de administração incompletas.** Não há rate limit/lockout de login e os POSTs administrativos não têm token CSRF explícito. O cookie tem `Secure` condicionado a `DEBUG`, mas a proteção depende de configuração correta. |
| M-05 | Média | **Exposição de dados e mensagens de erro.** A API pública retorna payload, destinatário, erro do provedor e identificador externo (`app/messages/schemas.py:22-32`). `response.text` completo do Brevo é persistido em `last_error` e incluído no log de exceção (`app/core/email_provider.py:80-81`), sem política de redação ou retenção. |
| M-06 | Média | **Configuração por projeto não governa o worker.** `Project.sqs_queue_url` e `Project.is_active` existem no modelo, mas o consumidor usa somente `settings.sqs_queue_url` e não verifica `is_active` (`app/worker/consumer.py:103-116`). Projetos desativados podem continuar sendo processados e filas configuradas no projeto podem ser ignoradas. |
| L-01 | Baixa/Média | **Limites operacionais ausentes.** Tamanho de payload, templates, schema e listas não é limitado no contrato; `limit` da API não tem validação declarada. O processamento sequencial de até 10 mensagens pode ultrapassar o visibility timeout de 60 s e causar redelivery concorrente. |

As severidades acima são conclusões estáticas baseadas no código atual. H-02 depende da política efetiva de IAM da fila compartilhada; essa política não está no checkout.

## Pontos faltantes que merecem atenção

Prioridade imediata antes de exposição externa:

1. Autenticação forte nos routers JSON e autorização por projeto; decidir se a chave, OAuth ou identidade de workload será o mecanismo oficial.
2. Vincular identidade do produtor ao projeto: filas por tenant, IAM por fila, ou envelope assinado/verificado. Não confiar em `MessageAttributes.project_id` isolado.
3. Idempotência ponta a ponta: chave de idempotência/unique constraint, estado de entrega, outbox ou provider idempotency key e reconciliação entre Brevo e banco.
4. Sanitizar o preview: `iframe sandbox`, CSP restritiva e separação de origem; não renderizar HTML editável no contexto administrativo.
5. Validar obrigatoriamente segredos, URLs e filas em startup; remover defaults de produção e separar configuração dev/prod.

Prioridade seguinte:

- Capturar e classificar todas as falhas do worker; registrar erro terminal, backoff, visibility extension e DLQ com IaC verificável.
- Adicionar RBAC, trilha de auditoria para alterações/resend/revogação, CSRF explícito, rate limit e expiração/revogação de sessões.
- Definir limites, retenção, mascaramento de PII e política para `payload`, destinatário e erro do provedor.
- Implementar readiness que verifique dependências, métricas de fila/entrega, alertas e correlação por mensagem.
- Documentar webhooks/bounces/reclamações, reconciliação, fallback de provider e o contrato de versão do produtor.
- Adicionar testes com PostgreSQL/SQS reais ou equivalentes de integração, concorrência, redelivery, crash entre provider e delete, autorização multi-tenant e XSS de preview.

## Estado documental e validação

Não existem neste checkout `AGENTS.md`, `SPEC.md`, `CHANGELOG.md`, `RELEASE_NOTES.md` ou histórico versionado do serviço. A documentação existente descrevia DLQ, Secrets Manager e IAM como forma de implantação, mas não contém IaC nem prova dessa configuração.

Checks executados em 2026-08-27:

- `poetry run pytest -q`: **18 passed**.
- `docker compose config --quiet`: **passou**; valida apenas a renderização do Compose.
- `poetry run ruff check .`: **falhou com 8 erros** na migration (`alembic/env.py` e `alembic/versions/...initial_schema.py`).
- `poetry run mypy app`: **falhou com 56 erros** em 16 arquivos.
- Não foi executado stack Docker, envio real ao Brevo, Postgres integrado, SQS AWS/ElasticMQ, DLQ, IAM ou browser E2E.
