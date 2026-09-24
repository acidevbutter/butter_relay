# Butter Relay — Resumo do Projeto

## Visão geral

**Butter Relay** é o serviço transacional de e-mail multi-tenant da DevButter. Aplicações
publicam jobs de "envie este e-mail" como tasks Celery numa fila Redis; o Relay consome essas
tasks, renderiza um template Jinja2 versionado, despacha via Brevo e registra cada tentativa. Um
dashboard administrativo renderizado no servidor permite gerir projetos, API keys, templates e o
log de mensagens sem tocar no banco.

- **Multi-tenant**: um único deploy atende todas as apps/clientes DevButter (Crate Diggr,
  Orbit, GranAI, sites de clientes, ...), cada um isolado como um `Project`.
- **Orientado a fila**: [Celery](https://docs.celeryq.dev/) com broker Redis, mesma instância
  Redis em todo ambiente (gerenciada pelo Coolify) — sem emulação de fila por ambiente para
  manter sincronizado com produção, ao contrário do setup ElasticMQ/SQS que este substituiu.
- **Só e-mail por enquanto**: `Message.channel` tem default `"email"`, reservado para um
  futuro canal `in_app`/`push` (nenhum outro mecanismo de entrega implementado).
- **Provider**: Brevo hoje, atrás do protocolo `EmailProvider`. Amazon SES é a próxima
  implementação documentada (autenticação IAM, sem API key separada).
- **Dashboard**: FastAPI + Jinja2 + HTMX, mesma imagem Docker e mesma camada de serviço da
  API — sem build de frontend separado.

## Stack técnica

| Camada | Escolha |
|---|---|
| Framework | FastAPI (async), gerenciado por Poetry, Python 3.12 |
| Persistência | SQLAlchemy 2.0 (async) + Alembic, PostgreSQL (asyncpg) |
| Fila | Celery com broker/result backend Redis, uma fila por projeto (`relay-<slug>`) + fila legada `relay-shared` |
| Templates | Jinja2 `SandboxedEnvironment` (corpo do template é conteúdo do usuário) |
| Provider de e-mail | Brevo (`app/core/email_provider.py`, protocolo `EmailProvider`) |
| Dashboard | Jinja2 + HTMX, auth por cookie de sessão (`bcrypt` + `itsdangerous`) |
| Observabilidade | APM via OTLP/HTTP direto ao New Relic, stdout logfmt e readiness probe |
| Testes | `pytest` + `httpx.AsyncClient`, SQLite em memória; Celery/Brevo mockados nos testes do worker |
| Lint/Type | `ruff` (E,F,I,UP,B,SIM), `mypy --strict` |

## Arquitetura e fluxo

### Fluxo de mensagem

1. App produtora → `send_task("butter_relay.process_message", kwargs={project_id, template_key,
   to, payload})` na fila do projeto (`relay-<slug>`, derivada do `slug` via
   `Project.queue_name`/`queue_name_for`) ou na fila legada `relay-shared` para produtores ainda
   não migrados.
2. Worker Celery (`app/worker/tasks.py::process_message_task`) consome a task; o id da task
   (`self.request.id`, estável entre retries) faz o papel de idempotência que o `MessageId` do
   SQS fazia antes.
3. Worker carrega `Project` + `Template` ativo do Postgres.
4. Insere `Message(status=pending)`; valida schema e renderiza subject/html.
5. `Message(status=processing, attempts += 1)`.
6. Envia via Brevo.
   - **Sucesso**: `Message(status=sent)` + `provider_message_id`; Celery confirma a task
     (`acks_late=True`).
   - **Falha**: `Message(status=failed, last_error)`; erros em `autoretry_for` (`NotFoundError`,
     `EmailSendError`, `ValidationFailedError`) disparam retry com backoff exponencial
     (`retry_backoff`, até `MAX_RETRIES=5`); payload malformado (`MalformedMessageError`) é
     logado e descartado sem retry, já que não é transitório.
7. **Idempotência**: se já existe `Message` com o mesmo `broker_message_id` e status `SENT`, o
   worker retorna sem reenviar.
8. **Blob/NFS-e**: se `payload.blob_id` estiver presente, o worker resolve o `public_url` no
   Butter Blob e injeta em `cta_link` antes de renderizar.
9. **Política de destinatários**: em `staging`/`preprod` só envia para domínios em
   `ALLOWED_RECIPIENT_DOMAINS`; `development` e `production` liberam todos.

### Deploy (formato, não IaC)

- **Coolify/Hostinger**: dois serviços (`relay-api` + `relay-worker`), mesma imagem Docker,
  `command` diferente (`uvicorn app.main:app` vs `celery -A app.worker.celery_app:celery_app
  worker`).
- **Postgres** gerenciado pelo Coolify, via `DATABASE_HOST`/`PORT`/`USER`/`PASSWORD`/`NAME`.
- **Redis** gerenciado pelo Coolify, via `REDIS_URL` — retries com backoff exponencial
  (`autoretry_for`, `MAX_RETRIES` em `app/worker/tasks.py`) substituem a redrive-policy-para-DLQ
  do SQS: uma task que falhou de vez vira `Message.status=failed` no Postgres, sem fila de
  dead-letter separada — `/admin/messages` é onde olhar para envios travados.
- Credenciais do DB, `BREVO_API_KEY` e `SESSION_SECRET` via variáveis de ambiente do Coolify.

## Modelo de dados

- **PROJECT** (`id`, `name`, `slug` UK, `external`, `default_sender_email`,
  `default_sender_name`, `is_active`) — a fila Celery do projeto é derivada do `slug`
  (`relay-<slug>`, ver `Project.queue_name`), não é mais uma coluna.
- **API_KEY** (`id`, `project_id` FK, `key_hash` UK, `label`, `expires_at`, `revoked_at`,
  `last_used_at`) — chave em texto puro só aparece na criação; só o hash é persistido/comparado.
  Projetos `external` recebem `expires_at` = criação + 30 dias; projetos internos não expiram.
- **TEMPLATE** (`id`, `project_id` FK, `key`, `subject_template`, `html_template`,
  `variables_schema` JSON, `version`, `is_active`) — `(project_id, key, version)` é único;
  publicar nova versão desativa a anterior.
- **MESSAGE** (`id`, `project_id` FK, `template_id` FK, `channel`, `to_email`, `payload`
  JSON, `status`, `attempts`, `provider_message_id`, `broker_message_id`) — uma linha por
  tentativa de envio = o log de e-mail.
- **ADMIN_USER** (`id`, `email` UK, `password_hash`)

Violações de unicidade (`slug`, `key_hash`, versão de template) viram HTTP `409`.

## Estrutura do projeto

```
app/
├── main.py            # wiring do FastAPI: middleware, exception handlers, routers, /health
├── settings/          # config.py (env via pydantic-settings), logging_config.py, middleware.py
├── core/              # dependencies.py (DbSession), decorators.py (log_call/log_errors),
│                      # exceptions.py (handlers auto-registrados), email_provider.py,
│                      # telemetry.py (OTLP), metrics.py (logfmt), blob_client.py (Butter Blob / NFS-e)
├── db/                # base.py, session.py, all_models.py (p/ autogenerate do Alembic)
├── projects/          # router/schemas/models/service/repository/dependencies
├── api_keys/          # credenciais por projeto (+ dep de auth CurrentApiKey)
├── templates_/        # templates de e-mail (subject + corpo Jinja2, versionados)
├── messages/          # log de envio (uma linha por tentativa)
├── auth/              # AdminUser, login/logout, dependência de sessão
├── admin/             # dashboard Jinja2+HTMX (router.py + templates/)
└── worker/            # worker Celery: celery_app.py, tasks.py, queues.py
alembic/               # migrations
docs/                  # API_SPEC.md, butter-relay-fluxos.md, auditoria de segurança, diagramas .mmd, prompts
scripts/               # curl_smoke_test.sh, seed_nota_fiscal_template.py
tests/                 # suíte pytest
```

Cada domínio (`projects`, `api_keys`, `templates_`, `messages`, `auth`) segue a mesma forma
de seis arquivos: `router.py`, `schemas.py`, `models.py`, `service.py`, `repository.py`,
`dependencies.py`. O `dependencies.py` de cada domínio guarda a fiação de DI (factories
`get_*_service` + aliases `Annotated[..., Depends(...)]`) e as deps de auth que o domínio
possui (`auth/` → `CurrentAdminUser`/`CurrentAdminUserApi`; `api_keys/` → `CurrentApiKey`).
Um domínio importa DI de outro via `app.<dono>.dependencies`, nunca de `router.py`.
`core/dependencies.py` mantém só `DbSession`.

## API HTTP

Duas superfícies distintas (não confundir):

| Prefixo | Auth | Operações |
|---|---|---|
| `/api/v1/projects` | `Authorization: Bearer <API key>` | listar, criar, consultar, alterar projetos |
| `/api/v1/api-keys` | Bearer | listar, criar, revogar chaves do projeto |
| `/api/v1/templates` | Bearer | listar, publicar, consultar, preview de template |
| `/api/v1/messages` | Bearer | listar e consultar log de mensagens |
| `/admin/login`, `/admin/logout` | — | sessão administrativa assinada |
| `/admin/projects`, `/admin/templates`, `/admin/messages` | cookie de sessão | dashboard HTML: gestão, preview, reenvio |
| `GET /health` | — | prova de processo |
| `GET /health/ready` | — | verifica o banco (503 se indisponível) |
| `GET /` | — | redirect para `/admin` |

Não há envio por rota HTTP — envio é sempre pelo worker Celery. Não há self-registration
público (API fechada); o primeiro admin é criado via container.

## Contrato do produtor (task Celery)

```python
celery_app.send_task(
    "butter_relay.process_message",
    kwargs={
        "project_id": "...",       # UUID string do tenant
        "template_key": "welcome_email",
        "to": "user@example.com",
        "payload": {"name": "Marcos"},
    },
    queue="relay-<slug>",  # fila do projeto; relay-shared para produtores legados
)
```

`project_id` identifica o tenant — é assim que o worker resolve `Project`/`Template` sem um
segundo hop de auth. `devbutter_backend` e `selolibertaria_backend` publicam nesse mesmo
contrato, com um cliente Celery próprio apontado para o mesmo `REDIS_URL`.

## Variáveis de ambiente

| Variável | Default | Notas |
|---|---|---|
| `ENVIRONMENT` | `development` | `development` \| `staging` \| `preprod` \| `production` |
| `DEBUG` | `false` | Relaxa a flag `Secure` do cookie quando `true` |
| `LOG_LEVEL` | `INFO` | |
| `DATABASE_URL` | `postgresql+asyncpg://relay:relay@shared-postgres:5432/relay` | |
| `REDIS_URL` | `redis://redis:6379/0` | Broker/backend do Celery |
| `RELAY_QUEUE_NAME` | `relay-shared` | Fila padrão/legada; cada projeto também tem a sua própria (`relay-<slug>`), consumida automaticamente |
| `BREVO_API_KEY` | — | Necessária para envios reais (senão `401`) |
| `ALLOWED_RECIPIENT_DOMAINS` | — | CSV; restringe destinatários em staging/preprod |
| `BUTTER_BLOB_BASE_URL` / `BUTTER_BLOB_API_KEY` / `BUTTER_BLOB_NAMESPACE` | — / — / `nfse` | integração Butter Blob (resolução de NFS-e) |
| `SESSION_SECRET` | placeholder dev | Definir um segredo real em produção |
| `SESSION_COOKIE_NAME` | `butter_relay_session` | |

## Rodar localmente (resumo)

```bash
cp .env.example .env
docker compose --profile dev up -d --build      # mailpit, api, worker
docker compose exec api alembic upgrade head
# criar primeiro admin via container (ver README §5)
```

- API: http://localhost:8010 (dashboard em `/admin`)
- Mailpit (UI de e-mail local): http://localhost:8025

Serviços do Compose dev: `mailpit` (1025/8025), `api` (8010→8000), `worker`. O Redis
(broker/backend do Celery) e o PostgreSQL compartilhado (`shared-postgres`) vêm do
`../infra/docker-compose.yml` na rede externa `devbutter_shared`, aliasado como `redis` para
todo worker DevButter compartilhar a mesma instância.

Arquivos Docker: `Dockerfile`, `Dockerfile.hostinger` (Coolify/Hostinger staging),
`docker-compose.yml` (dev), `docker-compose.hostinger.yml` (staging: `relay-api` +
`relay-worker` no mesmo Postgres `relay`).

## Testes

```bash
poetry install
poetry run pytest
./scripts/curl_smoke_test.sh   # smoke test HTTP end-to-end, idempotente
```

Cobre: CRUD de endpoints + casos de conflito `409` (projects/templates), versionamento de
template e validação de payload por JSON-schema, fluxos de login/sessão, decorators
`log_call`/`log_errors`, métricas, e um teste unitário do worker com Celery/Brevo mockados
(sucesso, falha do provider, mensagem malformada).

## Estado e roadmap

- **Versão**: `0.1.0` (2026-08-21). Sem tag Git.
- **Não comprovado em produção**: IAM (para SES)/Secrets Manager/config do provider são o
  formato pretendido, não prova do ambiente atual. A auditoria de segurança
  (`docs/auditoria-seguranca-2026-08-29.md`) e os prompts em `docs/prompts/` listam
  pendências: autenticação nas rotas `/api/v1` (guard nem sempre fiado), idempotência de
  entrega (unicidade de `broker_message_id`), `SESSION_SECRET` de produção, sandbox iframe no
  preview, redação de dados sensíveis nos logs, cobertura de testes messages/api_keys.
- **Recente**: APM OpenTelemetry direto ao New Relic e stdout logfmt legível; readiness probe;
  DI refatorada por domínio; resolução de NFS-e no blob ao enviar
  `nota_fiscal_emitida`; restrição de destinatários em staging/preprod; Mailpit no Compose dev;
  deploy conjunto API+worker no Coolify staging.
- **Planejado**: provider Amazon SES (autenticado por IAM); canal in-app/push está
  explicitamente fora de escopo — `Message.channel` existe só para não exigir migração
  futura.

## Documentação de referência

- `README.md` — guia completo com passo a passo local e diagramas Mermaid.
- `docs/API_SPEC.md` — mapa de navegação da API.
- `docs/butter-relay-fluxos.md` — fluxos de estado atual + revisão de segurança.
- `docs/diagrams/` — fontes `.mmd` (`architecture.mmd`, `message-flow-sequence.mmd`).
- `docs/auditoria-seguranca-2026-08-29.md` — auditoria de segurança.
- `docs/prompts/` — prompts de tarefas de hardening/correção.
- `CHANGELOG.md` / `RELEASE_NOTES.md` — histórico de mudanças.
