# Butter Relay — Resumo do Projeto

## Visão geral

**Butter Relay** é o serviço transacional de e-mail multi-tenant da DevButter. Aplicações
publicam jobs de "envie este e-mail" numa fila SQS; o Relay consome esses jobs, renderiza um
template Jinja2 versionado, despacha via Brevo e registra cada tentativa. Um dashboard
administrativo renderizado no servidor permite gerir projetos, API keys, templates e o log
de mensagens sem tocar no banco.

- **Multi-tenant**: um único deploy atende todas as apps/clientes DevButter (Crate Diggr,
  Orbit, GranAI, sites de clientes, ...), cada um isolado como um `Project`.
- **Orientado a fila**: AWS SQS em produção, ElasticMQ (compatível com a API SQS) em dev
  local — o mesmo código `boto3` nos dois casos.
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
| Fila | AWS SQS (prod) / ElasticMQ (dev local), via `boto3` |
| Templates | Jinja2 `SandboxedEnvironment` (corpo do template é conteúdo do usuário) |
| Provider de e-mail | Brevo (`app/core/email_provider.py`, protocolo `EmailProvider`) |
| Dashboard | Jinja2 + HTMX, auth por cookie de sessão (`bcrypt` + `itsdangerous`) |
| Observabilidade | Métricas em JSON estruturado (stdlib `logging`) ingeridas pelo New Relic, readiness probe |
| Testes | `pytest` + `httpx.AsyncClient`, SQLite em memória; `moto` para SQS |
| Lint/Type | `ruff` (E,F,I,UP,B,SIM), `mypy --strict` |

## Arquitetura e fluxo

### Fluxo de mensagem

1. App produtora → `SendMessage` no SQS com body `{template_key, to, payload}` e atributo
   `project_id` (UUID string). Acesso ao SQS é autorizado por **IAM**, não por API key.
2. Worker faz long-poll `ReceiveMessage`.
3. Worker carrega `Project` + `Template` ativo do Postgres.
4. Insere `Message(status=pending)`; valida schema e renderiza subject/html.
5. `Message(status=processing, attempts += 1)`.
6. Envia via Brevo.
   - **Sucesso**: `Message(status=sent)` + `provider_message_id`, `DeleteMessage` no SQS.
   - **Falha**: `Message(status=failed, last_error)`, mensagem SQS **não** é deletada →
     visibility timeout expira e reprocessa; após `maxReceiveCount` a redrive policy do SQS
     manda para a DLQ (nível de infra, não código da app).
7. **Idempotência**: se já existe `Message` com o mesmo `sqs_message_id` e status `SENT`, o
   worker retorna sem reenviar.
8. **Blob/NFS-e**: se `payload.blob_id` estiver presente, o worker resolve o `public_url` no
   Butter Blob e injeta em `cta_link` antes de renderizar.
9. **Política de destinatários**: em `staging`/`preprod` só envia para domínios em
   `ALLOWED_RECIPIENT_DOMAINS`; `development` e `production` liberam todos.

### Deploy (formato, não IaC)

- **ECS Fargate** (ou Coolify/Hostinger em staging): um serviço rodando
  `uvicorn app.main:app` (API/dashboard), outro rodando `python -m app.worker` — mesma
  imagem, `command` diferente.
- **RDS Postgres** para `DATABASE_URL`.
- **SQS** com redrive policy → DLQ.
- **Secrets Manager** para credenciais do DB, `BREVO_API_KEY`, `SESSION_SECRET`.
- `SQS_ENDPOINT_URL` fica **unset** na AWS para o `boto3` usar o SQS real via IAM role.

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
  JSON, `status`, `attempts`, `provider_message_id`, `sqs_message_id`) — uma linha por
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
│                      # metrics.py (JSON estruturado → New Relic), blob_client.py (Butter Blob / NFS-e)
├── db/                # base.py, session.py, all_models.py (p/ autogenerate do Alembic)
├── projects/          # router/schemas/models/service/repository/dependencies
├── api_keys/          # credenciais por projeto (+ dep de auth CurrentApiKey)
├── templates_/        # templates de e-mail (subject + corpo Jinja2, versionados)
├── messages/          # log de envio (uma linha por tentativa)
├── auth/              # AdminUser, login/logout, dependência de sessão
├── admin/             # dashboard Jinja2+HTMX (router.py + templates/)
└── worker/            # consumer SQS (python -m app.worker): __main__.py + consumer.py
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

Não há envio por rota HTTP — envio é sempre pelo worker SQS. Não há self-registration
público (API fechada); o primeiro admin é criado via container.

## Contrato do produtor (mensagem SQS)

Body:

```json
{ "template_key": "welcome_email", "to": "user@example.com", "payload": { "name": "Marcos" } }
```

Atributo de mensagem `project_id` (UUID string) identifica o tenant — é assim que o worker
resolve `Project`/`Template` sem um segundo hop de auth. Acesso ao SQS é via IAM.

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
docker compose --profile dev up -d --build      # elasticmq, mailpit, api, worker
docker compose exec api alembic upgrade head
# criar primeiro admin via container (ver README §5)
```

- API: http://localhost:8010 (dashboard em `/admin`)
- Mailpit (UI de e-mail local): http://localhost:8025
- Fila ElasticMQ local: `http://elasticmq:9324/000000000000/relay-shared`

Serviços do Compose dev: `elasticmq` (9324), `mailpit` (1025/8025), `api` (8010→8000),
`worker`. O PostgreSQL compartilhado (`shared-postgres`) vem do
`../infra/docker-compose.yml` na rede externa `devbutter_shared`.

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
`log_call`/`log_errors`, métricas, e um teste unitário do worker com SQS/Brevo mockados
(sucesso, falha do provider, mensagem malformada).

## Estado e roadmap

- **Versão**: `0.1.0` (2026-08-21). Sem tag Git.
- **Não comprovado em produção**: IAM/DLQ/Secrets Manager/config do provider são o formato
  pretendido, não prova do ambiente atual. A auditoria de segurança
  (`docs/auditoria-seguranca-2026-08-29.md`) e os prompts em `docs/prompts/` listam
  pendências: autenticação nas rotas `/api/v1` (guard nem sempre fiado), idempotência SQS,
  classificação de erros do worker, `SESSION_SECRET` de produção, sandbox iframe no preview,
  redação de dados sensíveis nos logs, cobertura de testes messages/api_keys.
- **Recente**: migração das métricas de CloudWatch EMF para JSON estruturado ingerido pelo
  New Relic; readiness probe; DI refatorada por domínio; resolução de NFS-e no blob ao enviar
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
